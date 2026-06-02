<!-- ARCHIVED from research/consensus-exit @ e402980, original date 2026-05-30. Design-space and warm-start priors research: trailing-stop above-equal prior; VWAP-Breakdown below-equal (literature contra); dimensionality vs data analysis (~1-knob budget). Feeds Phase 3b acceptance gate warm-start. -->
# Weighted-Consensus Exit Core — Design Space, Warm-Start Priors & Validatability

**Researcher:** prior-researcher (Agent Team `consensus-exit-research`, read-only/non-TDD)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2
**Scope:** Task #1 — (1) design-space survey of weighted/ensemble/scoring exit systems; (2) defensible warm-start priors per criterion; (3) dimensionality-vs-data tension; (4) honest-broker validatability verdict.
**Builds on:** `audit-soundness/audit/00-SYNTHESIS.md`, `findings/pillar3-empirical.md` (the DATA WALL), `findings/pillar2-optmethod.md` (T≈4 / overfitting), `01-reconstructed-vision.md`.
**Stance carried forward:** the decision log's "harden, don't migrate" verdict (`project_eut_cvar_migration_council_verdict`) and the two rejected-idea walls. **This experiment IS an exit-core migration** and inherits that scrutiny. I do NOT re-litigate whether to build it (user owns that); I make it sound or expose why it can't be.

> **Honest-broker framing.** Every literature claim is cited and tier-graded. Every empirical claim is graded `[Theoretical] / [Backtest] / [Out-of-sample] / [Live] / [Folklore]`. Facts, interpretation, and options are kept separate. I do NOT recommend an implementation path — I surface options + trade-offs. Where sources conflict I flag both.

---

## 0. TL;DR — the four answers in four sentences

1. **Design space:** weighted-ensemble / scoring / voting exit systems exist and are well-studied, but almost entirely as **entry/prediction** combiners (forecast combination, ensemble ML); a tunable-weight *exit-confirmation* core with an N-confirm latch is a defensible engineering synthesis of (a) forecast combination, (b) N-of-M consensus filtering, and (c) discrete-time hazard scoring — **none of which has a peer-reviewed, replicated track record specifically for intraday exits on rotating leveraged-ETF strategies** `[Backtest/Folklore at best]`.
2. **Warm-start priors:** the literature supports a *fixed equal-weight* starting point far better than it supports any *differentiated* prior — and for the four wired flags specifically, only the trailing-stop / drawdown-truncation leg has a clean theory prior; VWAP-Breakdown-as-exit is **contradicted** by the mean-reversion edge in the data and literature, so its honest prior is *low*, not high.
3. **Dimensionality-vs-data:** by López de Prado's own rule (≤1 free parameter per *independent* observation, ideally ≤0.5) and his effective-sample-size point (financial "n" is the **regime count**, not raw days), this design's honest data budget is **~5–15 independent regime units** (pillar3) — which supports **moving 0–1 weights off their priors with honesty, not the canonical 6-tunable vector (4 weights + θ + N, engine-integrator-confirmed) nor the 9–16 of the 7-voter branch.**
4. **Validatability verdict:** a weighted-consensus exit **cannot be statistically validated** at this data scale — it is structurally a **paper-trade OBSERVATION experiment**, not a statistical claim. The permission-to-tune design, *if* the permission gate is honestly calibrated, degenerates to "stay at the priors" almost always — which is the correct, evidence-respecting outcome and is itself the strongest argument *for* the design's safety and *against* expecting it to earn movement.

---

## 1. DESIGN SPACE — forms of weighted/ensemble/scoring exit systems

### 1.1 The taxonomy of forms

| Form | What it is | Free params | Where it's proven | Grade |
|---|---|---|---|---|
| **Linear score / weighted sum** | `score = Σ wᵢ·sᵢ`; exit if `score ≥ θ` | weights `wᵢ` + threshold `θ` | Forecast combination (Bates & Granger 1969 onward) — for **prediction**, not exit | `[Backtest]`, decades of it, but entry-side |
| **Weighted-majority / voting** | each criterion votes; weighted tally vs threshold; weights updated by recent performance | weights + update rule + window | Dynamic Weighted Majority (Kolter & Maloof 2007); Numin intraday ensemble (ICAIF'24) | `[Backtest]` intraday, **entry/prediction** |
| **Logistic / probabilistic** | `P(exit) = σ(Σ wᵢ·sᵢ)`; calibrated probability | weights + intercept | Discrete-time survival ≈ logistic (van de Schoot) | `[Theoretical]` sound; not exit-specific |
| **Hazard / discrete-time survival** | per-tick `h(t)` = P(exit at t \| survived to t); each tick a binary survivor decision | baseline hazard + covariate weights | ACD / duration models (Engle-Russell); Gompertz≈logit | `[Theoretical/Backtest]`, inter-trade durations, **not exit P&L** |
| **N-of-M consensus filter** | require ≥N of M criteria to agree across ticks before acting | N, M, per-criterion thresholds, tick count | Multi-indicator confirmation practitioner lit | `[Folklore — high adoption / low evidence]` |

**The experiment's proposed core is a hybrid:** a **linear weighted score** (tunable `wᵢ`) crossing a **tunable threshold `θ`** to register a "confirm," with a **tunable N-confirm latch across ticks** before liquidating. That is: a linear-score combiner wrapped in an N-of-M temporal consensus. This is a coherent, namable form — but see §1.3 on what the evidence actually covers.

### 1.2 What works for intraday exits on rotating leveraged-ETF strategies — the honest answer

**Almost nothing in the peer-reviewed literature directly addresses this exact cell.** The searches surfaced:

- **Weighted-majority ensembles for intraday trading** (Numin, ICAIF'24, arXiv 2412.03167) — `[Backtest]`, Tier 3 (arXiv preprint, accepted at a workshop, **not peer-reviewed journal**, date-flagged Dec 2024). Crucially, it predicts **discretised ten-candle returns every five minutes** — an **entry/direction prediction** task, NOT an exit-confirmation task. The reported result ("weighted-majority ensemble … improved accuracy as well as utility over any individual model, especially using the utility metric to dynamically re-weight over shorter windows") is *in-sample/experimental*; the abstract gives **no out-of-sample split, no sample size, no time period** — so it cannot support an OOS claim. `[single-source][Backtest, unreplicated]`.
- **Ensemble voting / multi-indicator confirmation** practitioner sources (Medium/Sword Red, Build Alpha, Tickeron) — Tier 4–5. These describe the *form* (consensus reduces false signals/whipsaw) but offer **no methodologically clean OOS evidence**; several explicitly warn the same approach "performs excellently on historical data but poorly in live trading" — i.e., they *self-report the overfitting risk*. `[Folklore — high adoption / low evidence]`.
- **Leveraged-ETF rebalancing dynamics** (arXiv 2504.20116, ~20yr SPY/NDX) — `[Backtest]`, Tier 3. Relevant finding: **"Daily-rebalanced LETFs enhance returns in momentum-driven markets, whereas infrequent rebalancing mitigates losses in mean-reverting regimes."** This *re-confirms the Kaminski-Lo regime split at the LETF level* and is consistent with pillar3's measured mean-reverting intraday regime.

> **Interpretation (labeled):** the design space for weighted-consensus *exits* on LETF strategies is essentially **un-validated territory in the formal literature**. The closest rigorous work is entry-side (Numin) or regime-characterisation (LETF rebalancing), not exit-P&L. Building the form is defensible *as engineering*; claiming the form is *proven to work for this use case* is not supportable from sources.

### 1.3 Conflict / tension to surface

The practitioner ensemble literature claims consensus **reduces whipsaw** (a point in the design's favour); the same literature **simultaneously** warns it **increases over-optimization risk** (a point against). Both are restatements of the same upstream trade-off — added structure cuts variance of the decision but multiplies the degrees of freedom you must then earn from data. I flag both rather than pick a winner. This is the precise tension §3 quantifies.

---

## 2. WARM-START PRIORS — what initial weight each criterion defensibly earns

> **Critical caveat up front.** The single most-supported prior in the literature is **NOT a differentiated weight vector — it is equal weights** (§2.1, the forecast-combination puzzle). Any *differentiated* prior below is a weaker, theory-flavoured deviation from that baseline and should be treated as such. I give the differentiated priors because the task asks for them, but the honest-broker position is that **equal-weight is the defensible default and the burden of proof is on any deviation.**

### 2.1 The baseline prior: EQUAL WEIGHTS (the forecast-combination puzzle)

`[Theoretical + Backtest, heavily replicated]` `[High]` — triangulated across three independent sources.

The **forecast combination puzzle** is the decades-replicated empirical finding that a **simple equal-weighted average of forecasts routinely beats estimated "optimal" weights** out-of-sample. Canonical chain: **Bates & Granger (1969)** → **Clemen (1989)** review → **Stock & Watson (2004)** → **Timmermann (2006)** → **Smith & Wallis (2009)** → **Claeskens et al. (2016)**. The accepted explanation is **finite-sample estimation error**: the variance of the estimated optimal weight is so large that the estimate lands *further* from the true optimum than a fixed equal weight does — "the benefit of using optimal weights may be offset by the estimation uncertainty of unknown weights, especially when large estimation noise is present."

> **Why this is the load-bearing prior for THIS experiment:** equal/fixed weights are the **shrinkage-toward-prior limit** of the permission-to-tune design. The puzzle says: on small samples, *don't move off the fixed weights* — you'll usually make it worse. That is simultaneously (a) the best available warm-start (equal, or theory-shaped, weights), and (b) a direct argument that the "tune as data accrues" half will, if honest, rarely fire. **This is handed to `tuning-methodologist` as the backbone of the shrinkage/pooling treatment** — equal-weight is one pole, freely-estimated is the other, and ridge/hierarchical shrinkage toward the prior is the principled middle.

### 2.2 Differentiated priors per wired criterion (weaker than §2.1; treat as deviations to be earned)

The four flags `resolve_trigger_priority` actually arbitrates (`math_engine.py:826-859`) are the honest voter set (pending engine-integrator confirmation of whether upstream sub-signals get promoted — see §3.2). Per-criterion prior basis:

| Criterion | Defensible prior direction | Basis | Empirical grade |
|---|---|---|---|
| **Trailing Stop** (drawdown truncation) | **Highest-conviction prior.** Warm-start at or above equal weight. | Mechanical left-tail truncation needs **no regime assumption** (synthesis §3, "halve drawdowns" half is grounded). Kaminski-Lo: the stop's *protective* leg is the one part that survives regime-independently. | `[Theoretical, sound]` |
| **VWAP Bleed Cut** (slow erosion) | Equal-ish weight. | Catches a distinct slow-erosion failure mode a sharp detector misses; volatility-scaled threshold is reasonable engineering. No clean academic prior either way. | `[Folklore — moderate adoption]` |
| **Take-Profit** | Equal-ish weight; **no first-principles prior for its *priority* over Bleed Cut** (audit OQ-1; comment cites an off-branch H2 doc). | TP timing is the regime-conditional leg per Kaminski-Lo; its value flips with autocorrelation sign. | `[Backtest, regime-conditional]` |
| **VWAP Breakdown** (as an EXIT) | **LOW prior — the literature leans the OTHER way.** | Below-VWAP price empirically tends to **mean-revert UP** (mean-reversion "nearly 30% nominal significance long / 43% short"; pure VWAP *crossover* signals score **4.7%/4.0% — below the 5% chance line**). Using below-VWAP as an *exit* runs opposite the documented edge (audit I-1(c): Leung-Zhang 2019's optimality proof is for a *mean-reverting* model). | `[Backtest]` against; `[Theoretical]` tension flagged in audit |

> **Honest-broker headline for the priors section:** the only criterion with a clean, regime-independent prior strong enough to *deserve* an above-equal warm-start weight is the **Trailing Stop**. The VWAP-Breakdown exit prior is, if anything, **negative** in the literature. So a "research-derived differentiated prior" that up-weights VWAP-Breakdown would be **citing the literature against its own grain** — exactly the citation-misuse tension the audit already flagged (I-1). **The defensible differentiated prior is: trailing stop ≥ others; VWAP-Breakdown ≤ others; everything else ≈ equal.** Anything more granular than that is unsupported by sources and would be a guess dressed as a prior.

### 2.3 Sub-signal priors (MC gate, parabolic ratchet, breakeven) — IF promoted to voters

The audit (vision §3.3, §6.3) is explicit that the **parabolic-ratchet velocity threshold, VWAP thresholds, K=150 neighbours, 5,000 MC paths** all **have no published or calibrated provenance** — they are practitioner heuristics. `[Folklore — no calibration source]`. Therefore:

- **Breakeven lock** — sound *as a latching one-way ratchet* (don't round-trip a banked gain); prior = "on," but it is a state latch, not a weighted vote, so forcing it into a linear score is a category stretch. `[Theoretical, narrow]`.
- **MC gate** — a *veto/permission* layer, not a vote (it suppresses an exit when history says "usually recovers"). Its own README concedes it is "least informative when most needed." Promoting it to a weighted positive voter would invert its designed role. **Prior: keep as a gate, not a voter.** `[Folklore + self-flagged weakness]`.
- **Parabolic ratchet** — un-calibrated; README says it "lives or dies by empirical evaluation that the 125-day window cannot deliver." **Prior: equal-or-below, explicitly provisional.** `[Folklore]`.

> **Replication status (all of §2.2–2.3):** No — none of the differentiated priors is independently replicated *for this exit use case*. The forecast-combination *equal-weight* baseline (§2.1) IS heavily replicated, but in forecasting, not LETF exits.
> **Regime sensitivity:** every differentiated prior except the trailing-stop truncation leg **flips sign with the momentum/mean-reversion regime** (Kaminski-Lo). pillar3 measured the operator's symphonies as **intraday mean-reverting** (pooled lag-1 AC = −0.036) — the regime where the *upside-capture* legs of these priors are neutral-to-harmful.

### 2.4 BINDING CONSTRAINT — the MC-independent fail-safe weight floor (and its conflict with the VWAP-Breakdown prior)

`[Engineering constraint, confirmed by engine-integrator from code — research/03-integration-map.md §5]` `[High]`

A weighted-consensus resolver does **not** touch the H-3 fail-CLOSED arming gap: a permanently-no-MC position never produces a `True is_trailing_stop_hit` flag for *any* resolver (`compute_exit_confirmation` returns `(count, False)` when `not armed`, `math_engine.py:503-504`, **upstream** of trigger resolution). **So the experiment's resolver swap leaves H-3 exactly where it is** — state this plainly; only a no-MC fail-safe arming path (audit fix-shape (a)) closes it. *Weaker, separate positive:* the swap **does** improve graceful degradation at the **combination** layer — a signal merely unavailable *this tick* contributes zero weight instead of the current silent rung-drop. That is not "fixes H-3."

This creates a **hard prior constraint**: the two **MC-independent** flags — **VWAP Breakdown (System A) + VWAP Bleed Cut (System B)** (`math_engine.py:744-787`) — must carry weight priors whose **sum ≥ the consensus threshold θ on their own**, so that a permanent MC outage (which silences the MC-dependent contributions) cannot starve the consensus score below θ and silently weaken protection. Any tuning permitted to move those weights must respect this **floor** (handed to `tuning-methodologist`: the permission gate must not tune a VWAP weight below the floor *regardless of statistical significance*).

> **Conflict-vs-evidence, surfaced not silently resolved:** the fail-safe floor pushes the **VWAP weights UP**, while the §2.2 literature finding pushes the **VWAP-Breakdown weight DOWN** (below-VWAP mean-reverts up; crossover signals below chance). These two pressures genuinely conflict. The honest reconciliation — *as an option, not a recommendation* — is that **Bleed Cut can carry more of the mandatory floor than Breakdown**, since Bleed Cut (slow-erosion detection) has no literature evidence *against* it, whereas Breakdown-as-exit does. **The wiring makes this split clean (engine-integrator, research/03 §1a):** System A (Breakdown) only arms once `safe_hwm >= vwap_cross_hwm_pct` — a *profit-protection regime that fires only after a gain is banked* (`math_engine.py:774`) — whereas System B (Bleed) fires purely on `current_return <= vwap_bleed_arm_pct` (`:782`), making Bleed the **more universally-available MC-independent protector** and therefore the better carrier of the `>= θ` floor. That keeps the MC-independent floor satisfied while respecting the negative Breakdown prior. But note the deeper tension: **the fail-safe requirement forces non-trivial weight onto exactly the criterion the literature is most skeptical of**, which is itself an argument that the four wired flags are not an ideal voter basis for a *weighted* scheme (they were designed for a *priority* scheme where ordering, not magnitude, carried the logic).

---

## 3. DIMENSIONALITY-vs-DATA — how many knobs can this design honestly carry?

### 3.1 The free-parameter inventory

| Knob | Count (4-flag core) | Count (if 7 sub-signals promoted) |
|---|---|---|
| Criterion weights `wᵢ` | 4 | 7 |
| Consensus threshold `θ` | 1 | 1 |
| N-confirm latch (ticks) | 1 | 1 |
| Per-criterion confirm thresholds (if tunable) | up to 4 | up to 7 |
| **Total free parameters** | **~6–10** | **~9–16** |

**Canonical count CONFIRMED by engine-integrator from code (research/03-integration-map.md §1a):** the "graft onto the existing engine" design replaces `resolve_trigger_priority`'s fixed order with a weighted score over the **SAME FOUR** boolean flags it takes today (`math_engine.py:836-841`); the "six layers" (vol scaling, time-squeeze, parabolic ratchet, breakeven lock) are **upstream scalars that shape the trailing-stop flag**, not independent votes, and Monte Carlo is a **gate/veto**, not a flag. So the canonical free-parameter count is **4 criterion weights + 1 threshold θ + 1 N-confirm = 6 tunables — lead with 6.** The weight vector carries a sum/scale constraint that removes ~1 DoF (effective ~5). The **7-voter branch** (promoting MC / parabolic ratchet / breakeven to first-class weighted voters → 9–16 tunables) is a **more invasive redesign the current wiring does not support without restructuring the upstream primitives** — it is the alternative, not the default. (Per-criterion confirm thresholds are tunable only if the design chooses to expose them; the minimal canonical design does not.)

### 3.2 The data bar — quantified from primary sources

`[Theoretical/methodological]` `[High]` — López de Prado, triangulated.

- **Parameters-per-observation rule (LdP, "10 Reasons Most ML Funds Fail," GARP whitepaper):** *"One parameter per observation is an absolute maximum; ideally aim for ratios below 0.5."* So **k free params ⇒ ≥ k independent observations (ideally ≥ 2k).**
- **The "observation" is NOT a raw day (LdP effective-sample-size point):** *"Ten years of daily data is not 2,500 independent observations—it might be closer to a few dozen meaningful regimes."* Financial data is non-IID; the **effective n is the regime count.**
- **Selection-inflation (DSR, Bailey & López de Prado 2014):** the expected *maximum* Sharpe across N trials grows with `√(2 ln N)`; minimum track-record length scales as `T* ∝ (σ²/μ²)(ln N + c)`. Tuning each weight is itself a trial, so each added knob *raises the bar it must then clear*.

### 3.3 The collision — stated plainly

pillar3 / pillar2 establish the available **independent** units:

- Live Guard-Alpha record: **~5–6 independent trading days** (22 episodes pseudo-replicate into 5 days).
- Autotuner validation fold: **T ≈ 4 usable days** (`autotuner.py:375-377`).
- Tail/regime-relevant independent events over 3 years: **~5–15 regime shifts** (the CVaR-divergence wall); ~37 tail days at 3yr (Yamai-Yoshiba).

> **The arithmetic (interpretation, labeled):** at LdP's *generous* 1-param-per-observation ceiling, **~5–15 independent units honestly supports moving ~0–1 weights off their priors** — not the **6-tunable canonical vector** (~5 effective DoF after the sum/scale constraint), and certainly not the 9–16 of the 7-voter branch. At the *prudent* ≤0.5 ratio it supports **moving essentially zero weights with statistical honesty** until many more regimes accrue. Every knob added past that ceiling is tuned on noise — the **exact T≈4 wall the autotuner already self-documents** (pillar2-optmethod (a): "selecting argmax over 500 noisy 4-sample statistics is close to selecting the trial with the luckiest 4 days").

**Answer to the sub-question "how many free parameters before tuning is indistinguishable from noise?":** roughly **one**, given the ~5–15 independent-regime budget and LdP's ratios. The design can *carry* 6 knobs (canonical) to 16 (7-voter branch) structurally; it can **honestly tune ~0–1 of them** at this data scale. The gap between those two numbers is the experiment's central risk.

---

## 4. VALIDATABILITY VERDICT (honest-broker)

### 4.1 The verdict, plainly

**A weighted-consensus exit CANNOT be statistically validated on this data, and cannot be for the foreseeable operating life of the product. It is inherently a paper-trade OBSERVATION experiment, not a statistical claim.** This is not a power problem to be fixed with patience; it is the **same structural data wall** the project already hit twice and walled off (`project_eut_cvar_migration_council_verdict`, `project_cvar_divergence_validation_wall`), now applied to weight selection instead of tail-risk action. The "harden, don't migrate" stance applies with full force: this experiment is an exit-core *migration*, and the council's binding reason — *un-validatable at this data scale* — transfers directly.

### 4.2 Why — three independent walls converging

1. **The estimation-error wall (forecast-combination puzzle, §2.1).** On samples this small, *estimated* weights are expected to **underperform fixed equal weights out-of-sample.** So even a perfectly-implemented tuner is, by the dominant theory, more likely to *degrade* the exit than improve it whenever it actually moves a weight. `[Theoretical+Backtest, High]`.
2. **The degrees-of-freedom wall (LdP, §3).** ~5–15 independent regime units vs 6–16 knobs violates the 1-param-per-observation ceiling several-fold. Selection across tuned weights inflates apparent performance (DSR). `[Methodological, High]`.
3. **The regime wall (Kaminski-Lo + pillar3).** The upside-capture legs of the priors are regime-conditional and the measured regime is the **unfavourable** (mean-reverting) one. You cannot validate a regime-conditional weight when you have ~5–15 regime observations correlated with the same scarce tail days. `[Backtest/Empirical, High]`.

### 4.3 The one genuinely positive structural property

`[Interpretation, labeled]` — **if** the permission-to-tune gate is calibrated as honestly as the existing BHY/Yekutieli + NN1 machinery (pillar2 graded that VALID and "the strongest piece of the stack"), then the permission gate will **almost never grant permission** at this data scale — exactly as the autotuner today reverts to fallback **78% of the time** (pillar3 §4). A weight that *stays at its prior because the data never earns the move* is the **correct, evidence-respecting behaviour**, and it makes the design **safe-by-degeneracy**: in the limit, weighted-consensus-with-honest-permission **collapses back to the fixed-prior (≈ equal-weight / current fixed-priority) system**, which is the very baseline the forecast-combination puzzle says is hard to beat. The design's safety and its inability-to-be-validated are **the same fact**.

> **The trap to name for Gate-1 (options, not a recommendation):**
> - **Option A — honest gate, rarely moves.** If the permission threshold is set rigorously, the system rarely leaves its priors; you get a safe re-parameterisation of today's engine plus an *observation log* of would-be weight moves. Value = a structured paper-trade observation instrument; NOT a validated alpha improvement. Trade-off: large engineering surface (new core, H-3 fail-safe interaction) for a system that mostly reproduces current behaviour.
> - **Option B — loose gate, moves often.** If the threshold is set loose enough to actually move weights on ~5–15 units, you are tuning on noise (forecast-combination puzzle + DSR) and have **re-imported the exact overfitting the BHY haircut exists to prevent**, on the *live exit path* rather than the offline tuner. Trade-off: visible "adaptivity," negative expected OOS value.
> - **There is no third option that is both adaptive AND validatable at this data scale.** That is the wall.

### 4.4 What WOULD change the verdict (for completeness, not a recommendation)

Only two levers move the wall, both named in pillar2 and both unimplemented: **(i) far more independent history** (raises the regime count — the binding `n`), or **(ii) CPCV / combinatorial purged CV** to manufacture multiple backtest paths from the same data and enable DSR/PBO-style *distributional* validation (López de Prado 2018 Ch.7). Neither converts the live weight-tuning into a statistically validated claim on the **current** ~5–15-regime budget; CPCV improves *selection-variance accounting*, it does not create independent regimes that do not exist.

### 4.5 Prerequisite flag for the build (cross-ref tuning-methodologist & engine-integrator)

`[Fact, from pillar2-optmethod OPT-INVALID-1 / synthesis H-1]` — **the existing permission-gate machinery this experiment would lean on is currently mis-wired for the CRRA-EU path:** `_haircut_select` hardcodes `compute_sortino_tstat` and ignores its `tstat_fn` param (`autotuner.py:1251`), so the deploy/reject FDR gate is scored on the wrong sampling distribution **live on the canonical THEORY bundle**. **Any "permission-to-tune" design that reuses the haircut gate inherits this defect.** This is a prerequisite to fix, not a property of the consensus design — handing to tuning-methodologist as a hard dependency; engine-integrator owns confirming the gate is on the consensus path.

---

## 5. Open questions (logged, not gating)

- **OQ-P1 [RESOLVED by engine-integrator]:** Canonical design keeps the **4-flag** voter set (6 tunables); promoting MC/ratchet/breakeven to first-class voters (9–16) is a more invasive alternative the current wiring does not support without restructuring upstream primitives. §3.1 updated.
- **OQ-P2:** Is the N-confirm latch *per-criterion* or *on the aggregate score*? Per-criterion multiplies DoF further. *(engine-integrator / tuning-methodologist)*
- **OQ-P3:** Does the permission gate reuse the BHY/Yekutieli haircut (inheriting H-1)? — tuning-methodologist (task #2) confirms **yes, by design** (per-weight hypothesis counted into `compute_n_effective`); so H-1 is a confirmed hard prerequisite (§4.5). *(tuning-methodologist owns the gate spec)*
- **OQ-P4 [Unverified]:** Numin's actual OOS split/sample/period — abstract omits them; would need the full ICAIF'24 paper to grade its evidence above `[single-source Backtest]`.
- **OQ-P5:** Is breakeven-lock (a one-way latch) even expressible as a linear-score weight without breaking its latching semantics? Likely a category mismatch.
- **Storage seam [ALIGNED with engine-integrator]:** warm-start PRIOR lands in `spec_facets` on the frozen THEORY bundle with a THEORY/STYLIZED_FACT `freeze_discipline`; the TUNED current value lands in the per-symphony params dict (effective BACKTEST_SELECTION); the **delta is the "moved off prior" honesty signal** Spec Critic surfaces. Consistent with §2 (only theory-shaped priors are defensible, never BACKTEST_SELECTION).

---

## 6. Sources (tier-graded, accessed 2026-05-30)

**Tier 1–2 (primary / named expert):**
- Kaminski, K. & Lo, A.W. (2014). "When Do Stop-Loss Rules Stop Losses?" *J. Financial Markets* 18:234-254. [SSRN 968338](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338) (403 on fetch — abstract via [RePEc/SIFR WP 0063](https://ideas.repec.org/p/hhs/sifrwp/0063.html), [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S138641811300030X)). **Stop-loss decreases expected return under random walk; adds value under momentum/positive serial correlation; stopping premium ∝ return persistence. Data: monthly US equity/bond 1950–2004; +50–100 bps/month during stop-out periods.** `[Backtest]`, Tier 2.
- Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio." [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551). E[max SR] selection-inflation + min track-record length. Tier 1/2.
- López de Prado, M. "The 10 Reasons Most Machine Learning Funds Fail" ([GARP whitepaper PDF](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf)). **"One parameter per observation is an absolute maximum; ideally aim for ratios below 0.5."** + effective-n = regime count, not raw days. Tier 2.
- López de Prado, M. (2018). *Advances in Financial Machine Learning* — CPCV (Ch.7), bagging variance reduction, "every backtest must report all trials." Summary notes: [reasonabledeviations.com](https://reasonabledeviations.com/notes/adv_fin_ml/). Tier 2 (book) / Tier 4 (notes).

**Forecast-combination puzzle (Tier 1–2 chain, triangulated 3 independent sources):**
- Bates, J.M. & Granger, C.W.J. (1969); Clemen, R.T. (1989) review; Stock, J. & Watson, M. (2004); Timmermann, A. (2006); Smith, J. & Wallis, K. (2009); Claeskens, G. et al. (2016). Via [Wang & Hyndman 2022 review, arXiv 2205.04216](https://arxiv.org/pdf/2205.04216), [Lee & Lee, UCR WP 202514](https://economics.ucr.edu/repec/ucr/wpaper/202514.pdf), [MDPI Econometrics 7(3):39](https://www.mdpi.com/2225-1146/7/3/39). **Equal weights beat estimated optimal weights OOS due to finite-sample estimation error.** `[Theoretical+Backtest, replicated]`, Tier 1–2.

**Tier 3 (preprint / community — date-flagged):**
- Numin: Weighted-Majority Ensembles for Intraday Trading (ICAIF'24, [arXiv 2412.03167](https://arxiv.org/abs/2412.03167)). **Entry/prediction, not exit; no OOS split/sample/period in abstract.** `[single-source Backtest]`, Tier 3, **not peer-reviewed journal**.
- Compounding Effects in Leveraged ETFs ([arXiv 2504.20116](https://arxiv.org/pdf/2504.20116)). Daily-rebalance LETFs help in momentum, hurt in mean-reversion — re-confirms Kaminski-Lo at LETF level. `[Backtest]`, Tier 3.
- Kolter & Maloof, Dynamic Weighted Majority (concept, via search synthesis). Tier 3.
- Discrete-time survival ≈ logistic; ACD duration models (Engle-Russell) — via [van de Schoot tutorial](https://www.rensvandeschoot.com/tutorials/discrete-time-survival/), [arXiv 2005.09166](https://arxiv.org/pdf/2005.09166). `[Theoretical]`, Tier 3.

**Tier 4–5 (practitioner — labeled Folklore):**
- Multi-indicator consensus / ensemble-voting trading blogs ([Build Alpha](https://www.buildalpha.com/trading-ensemble-strategies/), [Tickeron](https://tickeron.com/blogs/the-power-of-confirmation-stock-trading-in-ai-driven-strategies-11247/), [Medium/Sword Red](https://medium.com/@redsword_23261/multi-indicator-trend-confirmation-trading-system-ema-confluence-with-rsi-divergence-and-macd-8b5b79db80a7), [Above the Green Line](https://abovethegreenline.com/whipsaw-trading/)). Consensus reduces whipsaw BUT self-warns of over-optimization. `[Folklore — high adoption / low evidence]`.
- VWAP mean-reversion vs breakdown edge ([Volatility Box](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/), [TradingView/EdgeTools](https://www.tradingview.com/chart/ES1!/tVJcD92K-Everyone-Uses-VWAP-Wrong/)). Below-VWAP tends to mean-revert up; crossover signals below chance. `[Backtest/Folklore]`, corroborates audit I-1(c).

**Tier 1 (this repo, HEAD 8586ab2):** `math_engine.py:826-859` (resolve_trigger_priority), audit deliverables in `audit-soundness/audit/`.
