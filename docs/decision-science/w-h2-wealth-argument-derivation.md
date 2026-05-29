# W-H2 — Derivation of the wealth argument `W` fed to CRRA utility in the M1 autotuner objective

**Status:** DRAFT — revision 2 (post-critic-round-1).
**Revision 2 changes:** §3 unit-frame discipline + corrected codebase-identity line citations (C-1 + C-2 blockers); §3.3 NN1 form-as-persistent-facet clarification (S-3); §2.4 candidate-D disqualifier scoped to current walk-forward (S-4); §4.3 floor-sizing arithmetic corrected; §6.1 W-H4-B information-collapse residual added (S-2); §4.1 Carver/Chan citation softened (S-6).
**Lane:** Phase-1 M1 (CRRA-EU autotuner objective).
**Owner:** lead author (`team-lead`). Adversarial critic: `w-h2-critic` (`risk-engine-specialist`).
**Freeze discipline (NN1):** THEORY + STYLIZED_FACT only. No P&L-fitted choices.
**Binds:** `feature-plans/decision-science/phase-1/m1-crra-eu-autotuner-objective/plan.md` §W-H2 + §W-H4;
`feature-plans/decision-science/phase-1/red-test-2-m1-wealth-argument/plan.md` (the RED test that will pin this derivation).
**Companion artifact:** `tests/fixtures/m1-wealth-argument/derivation-fixture.json` (worked numerical examples).

---

## §1 — Question framing

The M1 autotuner objective replaces the v3 Sortino-plus-five-loss-aversion-multipliers
construction with a CRRA expected-utility (CRRA-EU) objective. Per `autotuner.py:289-301`
the per-trial significance statistic becomes the new `compute_crra_eu_tstat(U) =
mean(U)/(sd(U)/√T)` over a series `U_i = u(W_i)` where the CRRA utility function is

```
u(W; γ) = (W^(1-γ) − 1) / (1 − γ)        for γ ≠ 1
u(W; γ = 1) = ln(W)                       (the limit; log utility)
```

The construction is well-defined only when its **input** `W` is unambiguously
defined. The unresolved question — flagged as residual **W-H2** in the council
synthesis (§3.9, p.451-455) and pinned as binding by `red-test-2`'s discriminating-power
contract — is:

> **What does `W_i` mean operationally, and what units / horizon / aggregation /
> floor produce the series fed to `u(·)` inside the M1 autotuner branch?**

This memo answers six sub-questions, each provenanced by THEORY, MANDATE, or
STYLIZED_FACT:

1. **Operational identity** of `W_i` (post-trade NAV? return ratio? terminal wealth?).
2. **Units / scale** (dollars vs return-ratio vs log-ratio vs normalized).
3. **Per-trial vs walk-forward-fold aggregation** — what is one "trial" for purposes of `U_i`?
4. **Risk-free normalization** — net or gross of `r_f`?
5. **Horizon convention** — trade-exit, next-bar mark, fold-day-close, fold-end?
6. **`WEALTH_ARG_FLOOR`** — value + theoretical floor justification (the W-H4
   numerical-stability question, distinct from but consequent to W-H2).

The W-H2 closure required by `red-test-2` is the per-element formula `W_i = f(guard_alpha_i, …)`
applied to a fixture series. Items (1)-(5) above pin that formula; item (6) pins
the numerical-safety post-condition on it.

---

## §2 — Candidate formulations

Four candidate formulations for `W_i` were considered. Each is described, sourced,
and matched against the M1 plan's `A-4 wealth-argument consistency` constraint
(the same `W` is fed wherever CRRA is evaluated) and the H-1 NaN-propagation
correctness defect (`W` must be representable, finite, and amenable to a positive
named floor without distorting the dispersion of `U`).

### §2.1 Candidate A — Per-day **gross wealth ratio** of the AlphaBot policy: `W_i = 1 + r_i^{policy}`

The simple gross return ratio of the AlphaBot policy on the position for fold-day `i`:

```
W_i = 1 + r_i^{policy}     where r_i^{policy} = (P_i^{exit} − P_i^{entry}) / P_i^{entry}
```

Equivalent to the **per-period gross return** in standard utility-of-wealth
treatments. Pratt (1964) defines risk aversion on a wealth level `W`; the
single-period analogue used throughout the dynamic-programming literature
(Merton 1969; Samuelson 1969) defines `u(W_{t+1}/W_t) = u(R_t)` where `R_t = 1 +
r_t` is the per-period gross return. The CRRA functional form is closed under
this substitution: `(R_t)^(1-γ)/(1-γ)` is the canonical per-period felicity.

- **Source — Pratt, J. W. (1964). Risk Aversion in the Small and in the Large.**
  *Econometrica* 32(1-2), 122-136. DOI:10.2307/1913738. The CRRA functional family
  is defined on **wealth levels**, with the per-period analogue arising naturally
  for multiplicative return processes.
- **Source — Merton, R. C. (1969). Lifetime Portfolio Selection under Uncertainty:
  The Continuous-Time Case.** *Review of Economics and Statistics* 51(3), 247-257.
  DOI:10.2307/1926560. Demonstrates that for CRRA preferences the relevant
  per-period argument is the per-period gross return `R_t`; the multi-period
  problem is separable.
- **Source — Samuelson, P. A. (1969). Lifetime Portfolio Selection by Dynamic
  Stochastic Programming.** *Review of Economics and Statistics* 51(3), 239-246.
  DOI:10.2307/1926559. Same separability result; CRRA + i.i.d. gross-return
  process ⇒ per-period certainty-equivalent on `1 + r_t`.
- **Source — Campbell, J. Y., and Viceira, L. M. (2002). *Strategic Asset
  Allocation: Portfolio Choice for Long-Term Investors.*** Oxford University Press.
  ISBN 978-0-19-829694-2. Ch. 2 §2.1 (Power utility and the consumption-investment
  problem): "the felicity is `u(C_t)` where `C_t` is consumption, but in the
  intermediate-consumption-free formulation the relevant per-period argument is
  the gross return `R_t = 1 + r_t`."
- **Provenance label:** THEORY (utility-of-wealth literature for CRRA preferences
  under per-period gross returns).

### §2.2 Candidate B — Per-day **net wealth ratio** (risk-free-adjusted): `W_i = 1 + (r_i^{policy} − r_f)`

The same per-day construction but expressed as wealth growth net of the per-day
risk-free rate `r_f`. This is the "excess return" formulation.

- **Source — Cochrane, J. H. (2005). *Asset Pricing* (revised edition).** Princeton
  University Press. ISBN 978-0-691-12137-6. Ch. 1 §1.4: the stochastic discount
  factor representation `E[m_t R_t] = 1` uses **gross** returns `R_t`, not excess
  returns. Excess returns appear when an unconditional risk-premium decomposition
  is the goal — they are not the natural argument of an individual-investor
  felicity function.
- **Counter-source — Fama, E. F., and French, K. R. (1993). Common Risk Factors in
  the Returns on Stocks and Bonds.** *Journal of Financial Economics* 33(1), 3-56.
  DOI:10.1016/0304-405X(93)90023-5. Uses excess returns for **factor-model
  regressions**, but does not use them inside a CRRA felicity — those are two
  different questions (risk-premium decomposition vs. utility evaluation).
- **Stylized-fact friction.** Subtracting `r_f` produces a quantity that **can be
  negative even when the policy made money** (whenever `r_i^{policy} < r_f`). Then
  `W = 1 + (negative) < 1` is still positive in normal regimes but the
  interpretation drifts from "wealth at end of day" to "excess wealth net of cash
  benchmark." That breaks A-4 consistency: the Sortino branch in `autotuner.py:118`
  uses `SORTINO_TARGET_RETURN = 0.0` (operator decision PA-5, citing Sortino &
  van der Meer 1991), **not** `r_f`. Switching the CRRA branch to a `r_f`-adjusted
  argument introduces a benchmark inconsistency between branches.
- **Provenance label:** THEORY (factor-model literature) — but **inconsistent with
  the operator MANDATE** (`SORTINO_TARGET_RETURN = 0`) the project already adopted.

### §2.3 Candidate C — Per-day **log wealth ratio**: `W_i' = ln(1 + r_i^{policy})`, feeding `u(W_i')` directly

Use the continuously-compounded return as the wealth argument, dropping the `+1`
offset.

- **Source — Kelly, J. L. (1956). A New Interpretation of Information Rate.**
  *Bell System Technical Journal* 35(4), 917-926. DOI:10.1002/j.1538-7305.1956.tb03809.x.
  The Kelly criterion is `max E[ln(1 + r_t)]` — log utility on the gross return,
  **not** on `ln(1 + r_t)` itself. That is: Kelly already evaluates `u(W) = ln(W)`
  on `W = 1 + r_t`; the log is the utility transform, not a re-expression of `W`.
- **Mathematical disqualifier.** Substituting `W' = ln(1 + r)` into the general
  CRRA form `(W')^(1-γ)/(1-γ)` is ill-typed: `W'` is a log-return (can be
  negative; magnitude small) whereas CRRA `u(W) = (W^(1-γ) − 1)/(1−γ)` requires
  `W > 0`. Raising a negative log-return to a fractional power produces a complex
  number; raising a small positive log-return to a large negative `(1-γ)` for
  `γ > 1` produces a representability cliff dramatically worse than candidate A.
  This is the category error the council synthesis §3.9 W-H2 warns about
  ("guard-alpha is a *difference*, not a wealth ratio") generalized to any
  difference-like quantity.
- **Provenance label:** REJECTED on TYPE-CORRECTNESS grounds (the formulation is
  ill-typed against the CRRA functional form for non-log γ).

### §2.4 Candidate D — **Cumulative-NAV fold-end terminal wealth**: `W = ∏_{i=1}^{T} (1 + r_i^{policy})`, one scalar per trial

Use a single per-trial wealth scalar — the fold-end terminal wealth — and let `U`
be a one-element series.

- **Source — von Neumann, J., and Morgenstern, O. (1944). *Theory of Games and
  Economic Behavior.*** Princeton University Press. The von-Neumann-Morgenstern
  axioms define expected-utility on terminal-wealth outcomes; the **expectation**
  is taken across **states of the world**, not across time within a single
  realized path.
- **Statistical disqualifier (binding, scoped to the current walk-forward).** S-2
  requires `compute_crra_eu_tstat(U) = mean(U)/(sd(U)/√T)` where `T` is the
  **count of independent observations** contributing to the mean. Under the
  current 60/20/20 single-frozen-fold walk-forward (`autotuner.py:154-159`), a
  terminal-wealth scalar per fold yields `T = 1` per trial; `sd(U)` is
  undefined and the t-stat is structurally undefined. The BHY haircut
  (`autotuner.py:706-728`) requires a non-degenerate t-stat per trial. A
  future multi-fold-per-trial design (e.g. rolling k-fold, explicitly
  dismissed by council synthesis §3.5 as "no rolling k-fold needed" but
  architecturally supportable) would revisit this disqualifier; out of scope
  for M1.
- **Stylized-fact friction.** A single 125-day-fold realization cannot estimate
  the dispersion of policy outcomes; the dispersion is the very thing the BHY
  haircut is honest about. Terminal wealth collapses that dispersion.
- **Provenance label:** REJECTED on STATISTICAL-MACHINERY-COMPATIBILITY grounds
  (incompatible with binding condition S-2 of the council synthesis §4).

---

## §3 — Selection: Candidate A, with explicit provenance

**Selected formulation (canonical, decimal-fraction frame):**

```
W_i  =  max(WEALTH_ARG_FLOOR, 1 + r_i^{policy})                 (gross daily wealth ratio)
r_i^{policy}  =  triggered_return_i                             (the policy's realized per-day return; ≡ guard_alpha_i + eod_return_i by algebraic rearrangement)
```

The simpler statement is **`r_i^{policy} ≡ triggered_return_i`** — the policy's
realized per-day return on the position, defined in `_collect_sim_returns`
(`autotuner.py:672-695`: `eod_return = ticks[-1]["return"]` at line 673;
`triggered_return = tick["return"] + penalty` at line 688; `guard_alpha =
triggered_return - eod_return` at line 692) and identically structured in
`run_simulation` (`autotuner.py:752-777`: lines 753, 769, 777). The algebraic
rearrangement `triggered_return ≡ guard_alpha + eod_return` makes it visible
that **no new data is required**: the CRRA branch's `W` is reconstructible from
the same two-column fold-day record the Sortino branch already consumes
(`guard_alpha` and the EOD-benchmark return), via elementary addition.

### §3.0 Unit-frame discipline — `r^{policy}` is decimal-fraction inside this formula

The codebase carries per-day returns in **percent** units, established at the
producer boundary `synthetic_history.py:355`:

```
ticks.append({..., "return": agg_ret * 100.0, ...})
```

The `_PCT` suffix on the legacy Sortino-branch thresholds (`autotuner.py:97`
`MISSED_UPSIDE_THRESHOLD_PCT = 1.0`; `:105` `DRAWDOWN_THRESHOLD_PCT = 1.5`;
`:108` `DRAWDOWN_MIN_GAIN_PCT = 1.0`) and the magnitude of `tick["return"]`
values in `tests/autotuner/test_c3_*` / `test_c4_*` fixtures (values like
`0.5`, `2.0`, `-8.0`, `-50.0` — nonsensical as decimal fractions but normal
as percentages) both pin the codebase unit-frame as percent.

The CRRA wealth-ratio formula `W = 1 + r` **requires `r` in decimal-fraction
units**. A `+5%` day must produce `W = 1.05`, not `W = 6.0`. The unit conversion
is therefore explicit and named:

```
RETURN_PCT_TO_FRACTION = 100.0       (named module-scope constant; M1 implementation surface)
r_i^{policy} [fraction]  =  triggered_return_i [pct] / RETURN_PCT_TO_FRACTION
W_i  =  max(WEALTH_ARG_FLOOR, 1 + r_i^{policy} [fraction])
```

This memo defines the formula in the **decimal-fraction frame**. The M1
autotuner branch performs the percent→fraction conversion at the boundary
between `_collect_sim_returns` (which emits a series of percent-unit per-day
policy returns) and `compute_crra_eu_objective` (which consumes the
decimal-fraction series). The M1 implementation surface owns the
`RETURN_PCT_TO_FRACTION` constant; the W-H2 fixture's worked examples (§5 of
`tests/fixtures/m1-wealth-argument/derivation-fixture.json`) are all in the
decimal-fraction frame to match the formula domain.

**Why this matters for A-4 consistency.** The Sortino branch operates entirely
in percent and never converts; it consumes percent-unit `guard_alpha` directly
(`autotuner.py:752-797` arithmetic). The CRRA branch converts at its boundary
and the constants downstream (`WEALTH_ARG_FLOOR = 1e-3`, fixture values) are
fraction-unit. Cross-branch unit drift is prevented by the explicit named
conversion constant and a one-line documentation pin at the
`compute_crra_eu_objective` entry — flagged in M1 plan "Risk callouts" as a
named acceptance criterion.

> **Why this is A-4 consistent.** Wherever CRRA is evaluated, `W` is the same
> quantity: a positive per-period gross-return ratio. The Phase-1 autotuner branch
> is the only CRRA-evaluation site in M1; if a Phase-2 hold-branch CRRA evaluation
> is later added (council synthesis §3.5 makes this contingent and currently
> out-of-scope), the same definition extends without modification.

### §3.1 Provenance chain (THEORY, MANDATE, STYLIZED_FACT — no P&L fits)

| Decision facet | Choice | Provenance |
|---|---|---|
| Functional form of `W` | per-period gross return `1 + r_t` | **THEORY** — Pratt (1964); Merton (1969); Samuelson (1969); Campbell-Viceira (2002) Ch. 2. CRRA + per-period gross return is the canonical pair. |
| Units | dimensionless wealth ratio (≥ 0 by construction in the absence of leverage; `> 0` after the floor) | **THEORY** — CRRA's domain requires `W > 0`. |
| Per-trial `r_i^{policy}` reconstruction | `r^{policy} ≡ triggered_return_i` (equivalently `guard_alpha_i + eod_return_i`) | **STYLIZED_FACT** (codebase identity at `autotuner.py:672-695` lines 673, 688, 692 and `autotuner.py:752-777` lines 753, 769, 777); no new data required. |
| Unit-frame conversion | `r [fraction] = r [pct] / RETURN_PCT_TO_FRACTION`, `RETURN_PCT_TO_FRACTION = 100.0` | **STYLIZED_FACT** — `synthetic_history.py:355` writes `tick["return"] = agg_ret * 100.0` at the producer boundary; named conversion constant + project no-magic-numbers rule. |
| Risk-free adjustment | NONE (`r_f = 0`) | **MANDATE** — operator decision PA-5 fixes `SORTINO_TARGET_RETURN = 0.0` (`autotuner.py:117-118`, citing Sortino & van der Meer 1991, *J. Portfolio Management*). Maintaining the same baseline across branches preserves A-4 consistency. |
| Aggregation scope | one `U_i` per fold-day; mean over the `T` fold-days | **THEORY** — Merton-Samuelson per-period separability for CRRA; **STATISTICAL** — `T ≥ 2` required for the S-2 t-stat to be defined. |
| Horizon | fold-day close-to-close (one trading day) | **MANDATE** — the autotuner already records one realized-return value per fold-day; matching that grid keeps `r_i^{policy}` directly observable without re-simulating intraday. |
| Trial = fold = one autotuner Optuna trial | one trial spans the walk-forward fold (~25 days at `FROZEN_EVAL_RATIO = 0.20` of 125-day window) | **MANDATE** — preserves `autotuner.py:706-728`'s BHY pipeline unchanged. |

### §3.2 What is explicitly **NOT** chosen — and why

- **Not guard-alpha alone.** The council synthesis §3.9 W-H2 (verbatim p.451-455):
  "guard-alpha is a *difference*, not a wealth ratio." Feeding `W_i = guard_alpha_i`
  into CRRA violates the CRRA-domain constraint (`W > 0`) and is the original
  W-H2 defect this derivation closes.
- **Not net-of-`r_f`.** Candidate B introduces a benchmark inconsistency with the
  Sortino branch (`SORTINO_TARGET_RETURN = 0`). The H-6 disposition of the
  synthesis explicitly forbids inconsistent branch baselines.
- **Not log-return.** Candidate C is ill-typed against the CRRA functional form
  for `γ ≠ 1`.
- **Not terminal wealth.** Candidate D collapses `T → 1` and breaks S-2's
  t-stat construction.

### §3.3 Restriction to the M1 autotuner branch only — and the persistent NN1 facet

This derivation defines `W_i` **only** inside the M1 autotuner branch (i.e.
inside `run_simulation` once `spec_bundle.objective_kind == "crra_eu"`).

- The legacy Sortino branch keeps its existing five loss-aversion multipliers,
  consumes the same `guard_alpha_i` series **directly as a signed difference**,
  and is byte-identical to its current behavior (per M1 plan "Definition of Done"
  bullet on the `compute_sortino_tstat` regression).
- A Phase-2 exit-branch CRRA evaluation (hold vs. exit at decision time) is
  **out-of-scope** for Phase 1.

**The persistent NN1 facet is the DEFINITIONAL FORM, not the data source.** What
is frozen by THEORY/MANDATE/STYLIZED_FACT and persisted across phases is:

```
W  =  max(WEALTH_ARG_FLOOR, 1 + r^{policy})       (definitional form — frozen)
```

In Phase 1, `r^{policy}` is the **realized** per-day return on the
actually-triggered exit. In Phase 2 (if Finalist B unlocks), the hold-branch's
`r^{policy}` is the **simulated** per-day return on the no-exit counterfactual
path produced by `simulate_forward_paths` (`feature-plans/decision-science/phase-2/simulate-forward-paths/plan.md`).
The form is identical; the input data source differs. A-4 consistency is
satisfied because the form is the persistent NN1 facet, not the data source —
a downstream reader does not need a new facet for Phase 2.

---

## §4 — `WEALTH_ARG_FLOOR` — derivation (W-H4)

CRRA is **unbounded below** as `W → 0⁺` for `γ ≥ 1` (evaluation §A.1 H-1, p.95-103,
verbatim: "`u(W) = W^(1-γ)/(1-γ)` is unbounded below as W → 0+ for γ ≥ 1"). The
floor sits on the **input `W`**, never on the output `U`, for the reasons
enumerated at evaluation §A.1 (flooring `U` compresses the lower tail of the `U`-series,
shrinks `sd(U)`, and inflates the t-stat — anti-conservative bias).

### §4.1 Operating regime for `W`

- **Lower envelope on `r_i^{policy}`.** A position with a hard-stop at any
  trailing-stop layer can lose at most the gap-risk delta to the next tick.
  Within the AlphaBot architecture, the protective stop floor (the **safety floor**
  named in the README hazard table row "MC sentinel discipline (F-4 ★)") is
  designed never to be disabled. STYLIZED_FACT — the practitioner literature on
  systematic trailing-stop-protected long-only US-equity strategies (Carver
  2015, *Systematic Trading*; Chan 2013, *Algorithmic Trading*) establishes
  that in-distribution daily losses under such strategies do **not** approach
  `-99%`; the empirical worst single-day loss envelope is far less than `-50%`,
  leaving the `1e-3` floor (which corresponds to a `-99.9%` day) comfortably
  below any realistic in-distribution loss day. A **theoretical** worst case
  bounded by `−1` (total loss) is the conservative envelope; the floor's
  purpose is to handle pathological replay/backtest inputs, not to model the
  in-distribution loss distribution. (The specific percentile thresholds in
  Carver and Chan are not pinpointed here because the load-bearing claim is
  directional — `−99%` is not in-distribution — not a specific numerical bound.)
- **Theoretical lower bound on `W`.** `W ≥ 1 + r^{policy}_{min} ≥ 1 − 1 = 0` in
  the limit of total loss. The CRRA evaluation `u(0)` is `−∞` for `γ ≥ 1`. The
  floor must therefore be **strictly positive** and selected to keep
  `u(WEALTH_ARG_FLOOR)` representable in IEEE-754 double precision under the
  prudential γ range.

### §4.2 Prudential γ range

The council synthesis fixes `gamma` as a **frozen NN1 facet** — pre-registered,
not searched (M1 plan "Risk callouts / hazards" row NN1, verbatim: "`gamma` is
frozen by theory / mandate / pre-registration — never by P&L"). The fixture
worked examples below cover `γ ∈ {0.5, 1.0, 2.0}` per the kickoff brief; the
prudential operational range typically cited in the household-finance literature
is `γ ∈ [1, 5]`:

- **Source — Mehra, R., and Prescott, E. C. (1985). The Equity Premium: A Puzzle.**
  *Journal of Monetary Economics* 15(2), 145-161. DOI:10.1016/0304-3932(85)90061-3.
  Argues `γ ≤ 10` is the empirically defensible upper bound (any higher implies
  implausible risk-aversion).
- **Source — Campbell, J. Y. (2003). Consumption-Based Asset Pricing,** in
  *Handbook of the Economics of Finance, Vol 1B* (eds. Constantinides, Harris,
  Stulz). Elsevier, Ch. 13. ISBN 978-0-444-51363-2. Surveys the household-finance
  literature: `γ ∈ [2, 5]` is the central empirical range.

The floor must keep `u(WEALTH_ARG_FLOOR)` representable for the **most stressful**
prudential γ — i.e. the largest `γ` the operator might ever pre-register. We size
the floor against `γ_max = 5` for safety (with margin to 10).

### §4.3 IEEE-754 representability constraint

For `γ = 5`, `W^(1-γ) = W^(−4) = 1/W^4`. IEEE-754 double has a finite dynamic
range of ~1.8e308; the binding constraint is that `u(W)` and downstream
accumulations across `T` ≈ 25 fold-days remain comfortably inside that range.

At `WEALTH_ARG_FLOOR = 1e-3`:

| γ | `W^(1-γ)` at `W = 1e-3` | `u(WEALTH_ARG_FLOOR)` | IEEE-754 slack against 1.8e308 |
|---|---|---|---|
| 0.5 | 0.03162... | -1.937... | ~308 orders of magnitude |
| 1.0 | (log branch) | ln(1e-3) = -6.907... | full |
| 2.0 | 1000 | -999 | ~305 orders of magnitude |
| 5.0 | 1e12 | -2.5e11 | ~296 orders of magnitude |
| 10.0 (Mehra-Prescott cap) | 1e27 | ~-1.1e26 | ~282 orders of magnitude |

At the prudential ceiling `γ = 5`, `u(1e-3)` is approximately `-2.5e11`, leaving
~296 orders of magnitude of slack against IEEE-754 overflow. Even at the
Mehra-Prescott implausible-prudential cap `γ = 10`, slack remains ~282 orders
of magnitude. Numerical safety is not the binding constraint here — any
`WEALTH_ARG_FLOOR` in `[1e-4, 1e-2]` is numerically safe at prudential `γ ≤ 10`;
the operational rationale below selects the specific value.

### §4.4 Selected floor value: `WEALTH_ARG_FLOOR = 1e−3`

**`WEALTH_ARG_FLOOR = 0.001`** (one tenth of one percent of pre-trade wealth
remaining at end-of-day).

**Three-leg justification (none alone load-bearing; all three concur):**

1. **THEORY (IEEE-754 stability has wide tolerance; the floor is well inside it).**
   Per §4.3, the floor at `1e-3` leaves ≥ 282 orders of magnitude of IEEE-754
   slack at the Mehra-Prescott prudential γ-ceiling of 10. A tighter floor
   (`1e-4`) would consume one further order of magnitude; a looser floor
   (`1e-2`) would give up most of the operational-impossibility coverage in
   leg 2. The floor at `1e-3` is selected as the operationally meaningful
   value that comfortably sits inside the IEEE-754 safety envelope. The
   selection is **below** the analytic ceiling `(1e15)^(-1/4) ≈ 5.6e-4` (for
   `γ=5` with `u(W) > -1e15`) so simulated catastrophic-day tests can land at
   or below the floor without numerical blow-up; tighter selection would
   leave less room for fixture catastrophic-day cases.
2. **STYLIZED_FACT (operational impossibility region).** A realized per-day return
   of `r ≤ −99.9%` corresponds to a position whose end-of-day mark is essentially
   zero — a regime in which **the AlphaBot protective stop has, by construction,
   already fired** (architecture constraint: "the protective stop ALWAYS fires on
   ticks-below-stop alone in every sentinel-triggered branch," README hazard
   table row MC sentinel discipline). Encountering `W < 1e−3` in production is a
   *catastrophic failure of the safety floor itself*, not an in-distribution
   risk; the floor exists for numerical correctness in *replay* and *backtest*
   contexts where pathological synthetic days may be encountered. In-distribution
   single-day losses under a trailing-stop-protected long-only US-equity
   strategy do not approach `-99%`; the floor at `1e-3` is well below any
   realistic in-distribution loss day.
3. **MANDATE (no-magic-numbers project rule).** The constant is named, sourced
   in-line per `.claude/CLAUDE.md` "Coding Standards (project additions)," and
   the source comment cites this memo plus evaluation §A.1 H-1.

**The floor is applied to `W`, never to `U`.** Restating verbatim from evaluation
§A.1 (p.122-131): "the floor goes on the input wealth argument `W`, never on the
output utility `U` — flooring `U` directly compresses the lower tail of the
`U`-series, artificially shrinks `sd(U)`, and inflates the t-stat
`mean(U)/(sd(U)/√T)`, re-introducing an anti-conservative bias into the haircut.
Flooring `W` keeps `u(·)` monotone and continuous, so `sd(U)` remains the honest
dispersion of a series with a finite worst case."

### §4.5 What this floor is NOT

- **Not** an economic claim about minimum survivable wealth.
- **Not** a calibrated stop-loss level (the protective stops are the real safety
  floor; this is a numerical-stability guard).
- **Not** subject to Optuna search (NN1: spec-frozen, not searchable).
- **Not** applied to `U` under any circumstance.

---

## §5 — Horizon, units, per-trial-vs-fold

Restating §3.1 explicitly as standalone clauses, each provenanced:

### §5.1 Horizon: one trading day, close-to-close

- **MANDATE** — the autotuner records one realized return per fold-day at
  market close. Re-simulation at any finer grid would require new tick-data
  retrieval and is out-of-scope for M1.
- **THEORY** — Merton-Samuelson per-period CRRA separability is closed under any
  fixed period length; one trading day is the project's per-period grid.

### §5.2 Units: dimensionless wealth ratio (`W ∈ (0, ∞)`)

- **THEORY** — Pratt (1964); the CRRA family's argument is dimensionless. No
  dollar amount, no log, no normalization beyond the per-period gross-return
  construction.

### §5.3 Aggregation: one `U_i` per fold-day; `mean(U)` and `sd(U)` over `T` fold-days

- **THEORY** — Merton-Samuelson separability + von Neumann-Morgenstern axioms
  permit averaging felicity across the `T` independent (after purge + embargo)
  fold-days within one trial.
- **STATISTICAL** — `T ≥ 2` required for `sd(U)` to be defined (S-2 binding).
  AlphaBot's walk-forward folds carry `T ∈ [25, 75]` per fold (60/20/20 split of
  125 trading days; `autotuner.py:154-159`), well above the floor.

### §5.4 Pre- vs. post-`r_f`: gross (no `r_f` subtraction)

- **MANDATE** — `SORTINO_TARGET_RETURN = 0.0` (`autotuner.py:117-118`, citing
  operator PA-5 and Sortino & van der Meer 1991). The CRRA branch adopts the same
  baseline to preserve cross-branch consistency.

### §5.5 One trial = one Optuna trial = one walk-forward evaluation

- **MANDATE** — `autotuner.py:706-728` already wires `compute_*_tstat(U_series)`
  inside the BHY haircut at the trial level. Preserving the trial granularity
  keeps `compute_haircut_pvalue` + `benjamini_hochberg_adjust` + Yekutieli c(N)
  byte-identical to the Sortino branch (S-2-binding clause "BHY machinery 100%
  preserved").

---

## §6 — Limitations and open questions

The selection above closes the W-H2 question on which `W_i` to feed CRRA, but it
does not close every adjacent question:

### §6.1 Limitations (acknowledged)

1. **Hold-branch CRRA argument (Phase-2).** If Finalist B unlocks and a hold-vs-exit
   CRRA crossover layer ships, the hold-branch `W` is the **same `1 + r^{policy}`
   form evaluated on a hypothetical hold path** — but the data plumbing for the
   hold path is a Phase-2 deliverable (`phase-2/simulate-forward-paths/plan.md`).
   The Phase-1 derivation in this memo does not address it.
2. **Serial-correlation residual W-H5 (H-6, evaluation §A.6).** The t-stat
   `mean(U)/(sd(U)/√T)` inherits anti-conservatism from any lag-1 autocorrelation
   in `U_i`. M1 plan "Risk callouts" disposition: disclose-and-accept; HAC /
   Newey-West / `T_eff` correction is **explicitly out-of-scope for Phase 1**
   (M1 plan "Out of scope" row). This memo does not re-litigate W-H5.
3. **Gap-risk catastrophic days.** The floor at `1e−3` is set against IEEE-754
   stability, not against a calibrated catastrophic-day budget. Operating
   regimes where the protective stop fails (e.g. trading halts, gap-down opens
   beyond the stop) are out-of-distribution for the floor; in those regimes the
   floor merely prevents NaN, not loss.

4. **Floor-hit trial t-stat information-collapse (W-H4-B, new residual).**
   The floor preserves finiteness of `U` but **collapses the t-stat's
   discriminating power within any trial that contains a floor-hit day.**
   Mechanically: for `γ ≥ 2` and a floor-hit producing `U_floor ≈ -999` (γ=2)
   or `U_floor ≈ -2.5e11` (γ=5), `|U_floor|` overwhelms the other 24 fold-day
   utilities. Then `mean(U) ≈ U_floor / T` and `sd(U) ≈ |U_floor| / √(T-1)`,
   so the t-stat `mean(U) / (sd(U)/√T) ≈ sign(U_floor) · √((T-1)/T) ≈ -1`
   for any floor-hit trial regardless of the non-floor days' performance.
   Downstream, `compute_haircut_pvalue(t ≈ -1)` produces `p_adj ≈ 1` → the
   trial is dropped from BHY selection.

   This is **arguably correct policy behavior** — a trial that catastrophically
   lost on any day SHOULD be disqualified from selection — and is consistent
   with the loss-averse spirit of the legacy Sortino-branch objective. We
   document it explicitly as a residual rather than a bug. Mitigation paths
   (e.g. a max-utility-magnitude clamp at the floor) are rejected because they
   re-introduce the U-side floor anti-conservatism H-1 forbids.

   This residual is distinct from W-H4 (numerical stability) and from W-H5
   (serial-correlation anti-conservatism); we label it **W-H4-B
   (information-collapse on floor-hit trials)**. M1 plan disposition:
   disclose-and-accept; document in the autotuner docstring; no Phase-1
   remediation.

### §6.2 Open questions (NOT blocking M1)

- **OQ-W-H2-A — γ pre-registration value.** Selecting the actual `γ` to ship
  with M1 is the NN1 spec-freeze deliverable in `phase-1/nn1-spec-freeze-discipline/plan.md`,
  not this memo. This memo's fixture uses `γ ∈ {0.5, 1.0, 2.0}` to span the
  candidate region; the operator's pre-registered value will be one of these or
  a value in the same prudential range.
- **OQ-W-H2-B — alternative envelope sources for the floor.** A more aggressive
  floor (e.g. `WEALTH_ARG_FLOOR = 1e−4`, allowing simulated −99.99% days)
  preserves IEEE-754 stability for `γ ≤ 5` but consumes the slack margin. We
  conservatively selected `1e−3`. A future cycle may revisit if the prudential
  γ range is narrowed below 5.
- **OQ-W-H2-C — A-4 consistency check across branches.** A runtime invariant
  (asserted in `derive_wealth_argument`'s docstring contract) is the natural
  carrier of A-4 — but a formal invariant test against multiple call sites only
  exists *after* the Phase-2 hold-branch ships, by construction.

---

## §7 — References

Listed in citation order; year, venue, and DOI / stable URL provided where
available. All sources are Tier-1 or Tier-2 per the researcher source-quality
hierarchy.

1. **Pratt, J. W.** (1964). Risk Aversion in the Small and in the Large.
   *Econometrica* 32(1-2), 122-136. DOI: 10.2307/1913738.
2. **Merton, R. C.** (1969). Lifetime Portfolio Selection under Uncertainty: The
   Continuous-Time Case. *Review of Economics and Statistics* 51(3), 247-257.
   DOI: 10.2307/1926560.
3. **Samuelson, P. A.** (1969). Lifetime Portfolio Selection by Dynamic
   Stochastic Programming. *Review of Economics and Statistics* 51(3), 239-246.
   DOI: 10.2307/1926559.
4. **Kelly, J. L.** (1956). A New Interpretation of Information Rate. *Bell
   System Technical Journal* 35(4), 917-926. DOI: 10.1002/j.1538-7305.1956.tb03809.x.
5. **von Neumann, J., and Morgenstern, O.** (1944). *Theory of Games and Economic
   Behavior.* Princeton University Press.
6. **Mehra, R., and Prescott, E. C.** (1985). The Equity Premium: A Puzzle.
   *Journal of Monetary Economics* 15(2), 145-161. DOI: 10.1016/0304-3932(85)90061-3.
7. **Sortino, F. A., and van der Meer, R.** (1991). Downside Risk. *Journal of
   Portfolio Management* 17(4), 27-31. DOI: 10.3905/jpm.1991.409343.
8. **Cochrane, J. H.** (2005). *Asset Pricing* (revised edition). Princeton
   University Press. ISBN 978-0-691-12137-6.
9. **Campbell, J. Y., and Viceira, L. M.** (2002). *Strategic Asset Allocation:
   Portfolio Choice for Long-Term Investors.* Oxford University Press.
   ISBN 978-0-19-829694-2.
10. **Campbell, J. Y.** (2003). Consumption-Based Asset Pricing, in *Handbook of
    the Economics of Finance, Vol. 1B* (eds. Constantinides, Harris, Stulz).
    Elsevier, Ch. 13. ISBN 978-0-444-51363-2.
11. **Fama, E. F., and French, K. R.** (1993). Common Risk Factors in the Returns
    on Stocks and Bonds. *Journal of Financial Economics* 33(1), 3-56.
    DOI: 10.1016/0304-405X(93)90023-5.
12. **Carver, R.** (2015). *Systematic Trading: A Unique New Method for Designing
    Trading and Investing Systems.* Harriman House. ISBN 978-0-857-19444-0.
13. **Chan, E. P.** (2013). *Algorithmic Trading: Winning Strategies and Their
    Rationale.* Wiley. ISBN 978-1-118-46014-6.
14. **`docs/handoff/decision-science-council-synthesis.md`** — internal:
    §2.1, §3.1, §3.5, §3.9 W-H2, §4 binding conditions S-1/S-2/S-3, §8 test 2.
15. **`docs/handoff/decision-science-v3-and-divergence-evaluation.md`** — internal:
    §A.1 hole H-1, §A.6 hole H-6 (W-H5), §A.7 hole H-7.
16. **`feature-plans/decision-science/phase-1/m1-crra-eu-autotuner-objective/plan.md`** —
    §W-H2, §W-H4, "Definition of Done."
17. **`feature-plans/decision-science/phase-1/red-test-2-m1-wealth-argument/plan.md`** —
    the RED test that will pin this derivation.
18. **`autotuner.py:117-118`** — `SORTINO_TARGET_RETURN = 0.0` operator MANDATE
    (PA-5) and its Sortino & van der Meer 1991 source comment.
19. **`autotuner.py:97, 105, 108`** — `_PCT`-suffixed loss-aversion thresholds
    confirming the codebase unit-frame is percent.
20. **`autotuner.py:672-695`** — `_collect_sim_returns`: codebase identity for
    `eod_return` (line 673), `triggered_return` (line 688), `guard_alpha = triggered_return − eod_return` (line 692).
21. **`autotuner.py:752-777`** — `run_simulation`: same identity (lines 753, 769, 777).
22. **`autotuner.py:154-159`** — `TRAIN_RATIO`/`VALIDATION_RATIO`/`FROZEN_EVAL_RATIO`
    60/20/20 walk-forward split anchoring the per-trial T-count.
23. **`synthetic_history.py:355`** — `tick["return"] = agg_ret * 100.0` producer
    boundary establishing the codebase percent frame.
24. **`math_engine.py:30-54`** — `_reject_non_finite` policy that the
    `WEALTH_ARG_FLOOR` guard upholds.
