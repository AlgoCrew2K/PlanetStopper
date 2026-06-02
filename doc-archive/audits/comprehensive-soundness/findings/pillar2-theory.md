<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Theory findings: citation-misuse tensions (I-1a/b/c) informational; "SOUND IN FORM, ALPHA-HALF UNPROVEN" verdict recorded in memory/project_adaptive_exit_direction.md. -->
# Pillar 2 — Risk-Math Theory Soundness

**Auditor:** theory-auditor (audit-soundness team)
**Snapshot:** HEAD 8586ab2, shared worktree `.claude/worktrees/audit-soundness`
**Scope:** Is each risk-math layer in `math_engine.py` THEORETICALLY SOUND per the quant-finance literature? (Coding correctness = mathimpl-auditor; empirical performance = empirical-auditor.)
**Method:** Read all of `math_engine.py` (1727 lines). Verified every load-bearing citation against the primary source via WebSearch. Verdict scale: PROVEN / PRACTICED-BUT-UNPROVEN / FOLKLORE / UNSOUND.

> **Headline verdict.** No layer is UNSOUND in form — the math is competently constructed and the fail-safe direction is correct throughout. But the family's *central value claim* (early exits net-recover value: "halve drawdowns + capture upside") rests on a regime condition (momentum/serial correlation) that the project's OWN cited literature says is **not universal** and is **not established for the operator's rotating-ETF universe**. Several layers cite papers that, read carefully, are *cautions against* the technique rather than endorsements of it. The deepest verdict is: **theoretically defensible as a risk overlay; theoretically UNPROVEN as an alpha generator; and structurally un-validatable at this data scale for the tail-risk pieces** — which the project itself already concluded and walled off. Those walls were theoretically correct.

---

## Layer-by-layer verdicts

### 1. Volatility scaling — `calculate_20d_vol` (`math_engine.py:1104-1136`), `calculate_14d_atr_pct` (`:1139-1193`), `compute_active_trailing_stop` (`:328-372`)

**What it assumes.** (a) Recent realized volatility forecasts near-term volatility (volatility clusters / is persistent); (b) a stop set proportional to volatility (`max(vol × multiplier, floor)`, `:369`) correctly separates "ordinary noise" from "a real move."

**Does the assumption hold intraday on rotating ETFs?** Volatility clustering is one of the most robust stylized facts in finance (ARCH/GARCH, Engle 1982; Cont 2001 "stylized facts"). It holds at intraday horizons too. The 20-day RiskMetrics window (`:64`) is the industry standard. The weakness is structural and acknowledged in the vision (§5.2): vol persistence *breaks at regime transitions* — exactly when protection matters most — and a 20-day trailing window lags a vol spike by construction.

**Verdict: PROVEN (as a sizing principle).** Volatility-scaled position/stop sizing is mainstream and theoretically grounded (volatility targeting: Moreira & Muir 2017, "Volatility-Managed Portfolios," *J. Finance*; vol clustering: Cont 2001). The *specific* multiplier band (MULT_OPEN 1.5 → MULT_CLOSE 0.5, `:246-247`) is a calibration choice, not a theorem — but the underlying "wider stop for noisier asset" logic is sound.
- **Empirical Evidence:** `[Out-of-sample backtest]` for vol targeting generally (Moreira-Muir); the specific bands here are `[Backtest]`/un-calibrated.
- **Replication Status:** Vol clustering — replicated thousands of times. The Planet Stopper band — Unknown.
- **Regime Sensitivity:** Fails at vol regime shifts (lagging window); fails when the symphony's holdings rotate to a basket with different vol than the trailing 20d implies (the vol is portfolio-level on *current* weights but estimated on history that may have held different weights — a subtle look-back consistency issue worth flagging to mathimpl-auditor).

---

### 2. Log-time / √-time squeeze decay — `compute_time_squeeze_decay` (`:294-325`), constants `:231-250`

**What it assumes.** The "remaining-session return uncertainty" shrinks as `√(1−t)`, so stop tightness should follow `f(t) = 1 − √(1−t)`. Claimed THEORY with zero free parameters, citing **Danielsson & Zigrand (2003)** (`:241-242`).

**Verification of the cited source.** I read the citation against the primary source. Danielsson & Zigrand (2003), *On time-scaling of risk and the square-root-of-time rule* (LSE FMG DP-439; later *J. Banking & Finance*), is **a paper about the BIASES of the √-time rule, not an endorsement of it.** Its central finding: the √-time rule **systematically underestimates risk under persistent (momentum) processes and overestimates under mean-reverting processes**, with bias growing in the horizon; fat tails and vol clustering further distort it. ([Danielsson-Zigrand 2003](https://eprints.lse.ac.uk/24827/1/dp439.pdf))

**This is a citation tension, flagged explicitly (per HARD SCOPE rule 4).** The functional form `√(1−t)` is the correct scaling for the SD of remaining returns of an i.i.d. constant-variance process — that much is genuine textbook theory (Brownian scaling). But the engine cites a paper whose actual content is that this scaling **fails precisely under the serial-correlation regimes the rest of the system bets on existing** (momentum). You cannot simultaneously claim "momentum exists, so stops add value" (Layer 8 / Kaminski-Lo) AND "√-time is valid THEORY for our decay curve" (which assumes i.i.d., no momentum). The two load-bearing assumptions are in mild internal contradiction.

**Verdict: PRACTICED-BUT-UNPROVEN, with a citation-misuse flag.** The *shape* (concave, monotone, front-loaded, tightening into the close) is a reasonable and parameter-free heuristic, and tightening as time runs out is defensible on first principles (less time for recovery). But labeling it THEORY on the strength of Danielsson-Zigrand is a misread — that paper argues the i.i.d. √-time assumption is *unreliable* in real return series. The honest label is "first-principles-motivated heuristic," not "proven theorem."
- **Empirical Evidence:** `[Theoretical]` (the i.i.d. derivation) but the i.i.d. premise is `[contradicted]` by the same cited paper for real returns.
- **Replication Status:** N/A (no empirical claim made).
- **Regime Sensitivity:** Under-tightens (leaves stop too wide) when returns are persistent/trending — the favorable regime for the overall thesis — so the decay curve is least aggressive exactly when momentum would justify riding longer anyway; arguably self-correcting, arguably mis-specified. Worth empirical-auditor's attention.

---

### 3. Parabolic ratchet (PARA-ARM) — `compute_para_arm_decision` (`:268-291`), arm branch in `compute_active_trailing_stop` (`:370-371`)

**What it assumes.** A fast up-move (`velocity = current_return − prev_return ≥ threshold`, `:289-290`) is unsustainable, so tighten the stop to lock the spike. Default threshold 2.0pp and squeeze multiplier have **no published calibration** (vision §3.3, OQ-5; confirmed — there is no source comment on the threshold value, only on the mechanism).

**Does it hold?** "Parabolic moves mean-revert" is a practitioner belief with mixed academic support. Short-horizon reversal after extreme moves is documented (Lehmann 1990; Jegadeesh 1990, "Evidence of Predictable Behavior of Security Returns," *J. Finance* — weekly reversals), but it is noisy, asset-dependent, and competes directly with short-horizon *momentum/continuation*. There is no theorem that a 2pp/tick velocity spike predicts reversal on a rotating-ETF basket.

**Verdict: FOLKLORE — high adoption / low evidence.** The mechanism is a standard discretionary-trader heuristic ("parabolic moves don't last") with no first-principles or calibrated provenance on file. It is tuned on the same thin 125-day window, placing it inside the overfitting blast radius. Not unsound (tightening a stop is fail-safe-directional), but unproven and un-anchored.
- **Empirical Evidence:** `[Folklore]`.
- **Replication Status:** Short-horizon reversal as a phenomenon — partially replicated but regime/asset-dependent; *this specific rule* — No / Unknown.
- **Regime Sensitivity:** Actively harmful in a sustained breakout (tightens the stop and exits a genuine momentum run early — the exact "fail to capture upside" failure mode against the north star).

---

### 4. Breakeven lock — `compute_breakeven_update` (`:375-448`)

**What it assumes.** After N consecutive ticks (5, `HWM_HOLD_TICKS_THRESHOLD` `:260`) holding above a vol-derived activation threshold, latch a one-way floor at 0.0 ("never give back a banked gain"). Cites Fu & Zhang (2012) (`:423-424`) for trailing-stop *construction*, not for the breakeven rule itself.

**Does it hold?** This is loss-aversion / disposition-effect risk management — behavioral, not a no-arbitrage result. "Don't let a winner become a loser" has no expected-value justification (in a frictionless EV sense, a one-way floor can only *reduce* expected terminal wealth under a random walk — same logic as Kaminski-Lo's random-walk case). Its justification is operator psychology and drawdown control, which is a legitimate *objective* but not a return-enhancing theorem.

**Verdict: PRACTICED-BUT-UNPROVEN.** Widely practiced, theoretically coherent *as a risk-preference expression* (it directly serves the "halve drawdowns" half of the north star), but it does not have a proven-value basis and under a random-walk regime it is expected-value-negative — it buys drawdown reduction by sacrificing some expected upside. That trade-off is exactly what a risk-averse (CRRA) operator wants, so it is internally consistent with the project's stated objective. The latching/one-way invariant (`:413-415`) is correctly implemented as a ratchet.
- **Empirical Evidence:** `[Theoretical]` for the EV cost; `[Folklore]` for the benefit.
- **Replication Status:** N/A.
- **Regime Sensitivity:** Costs the most in choppy/mean-reverting regimes where price oscillates around the locked floor (whipsaw exits).

---

### 5. VWAP signals — `compute_vwap_signals` (`:590-642`), `compute_vwap_breakdown_update` (`:684-789`), `compute_vwap_bleed_arm_threshold` (`:650-676`)

**What it assumes.** Trading below VWAP = "buyers lost control today" = a breakdown/exit signal. System A (profit-protection cross, gated on `safe_hwm ≥ vwap_cross_hwm_pct`, `:774`) and System B (slow bleed below a vol-scaled negative threshold). The regime-switch gate cites **Leung & Zhang (2019)** + **Peskir (1998)** (`:751-773`), and the code itself honestly flags this as an *interpretive extension*, not a proven theorem (`:769-773`).

**Verification of the cited sources.**
- **Leung & Zhang (2019)**, *Optimal Trading with a Trailing Stop* (*Applied Math & Optimization* 80, 669-698, [DOI 10.1007/s00245-019-09559-0](https://link.springer.com/article/10.1007/s00245-019-09559-0)): I confirmed it proves optimality of a trailing-stop + limit-order structure — but under an **exponential Ornstein-Uhlenbeck (mean-reverting)** price model. The optimality result is *for a mean-reverting asset*, which is a specific (and arguably opposite) regime from the trend-following premise. Citing it for a rotating-ETF momentum overlay is a stretch the code's own honest-flag half-acknowledges.
- **VWAP as a signal:** the strongest empirical evidence I found ([QuantifiedStrategies synthesis of large-scale VWAP testing](https://www.quantifiedstrategies.com/volume-weighted-average-price/)) is that **below-VWAP price mean-reverts UPWARD** (oversold → bounce) — the *mean-reversion* reading. The engine uses below-VWAP as an **exit/breakdown** signal (a momentum/continuation reading). **These are opposite interpretations of the same signal.** The documented edge is in fading the deviation (buy below VWAP), not in exiting on it. This is a genuine theoretical tension: the layer treats a signal with mean-reversion evidence as if it were a continuation signal.

**Verdict: PRACTICED-BUT-UNPROVEN, with a direction-of-signal flag.** VWAP is a legitimate, widely-used microstructure benchmark; "below VWAP = weakness" is standard trading-desk practice. But (a) the optimal-stopping citation is for a mean-reverting model, not the system's regime; (b) the academic VWAP edge points the *other direction* (reversion, not breakdown). The two-detector decomposition (sharp cross vs. slow bleed) is sensible engineering. The thresholds (`VWAP_WEIGHT_THRESHOLD` 0.5, bleed clamps `:646-647`) are un-calibrated heuristics.
- **Empirical Evidence:** below-VWAP signal — `[Out-of-sample backtest]` but for the *reversion* direction; the *breakdown/exit* use — `[Folklore]`.
- **Replication Status:** VWAP reversion — replicated at scale; VWAP-as-exit — Unknown.
- **Regime Sensitivity:** A below-VWAP exit fires into exactly the dips that the reversion literature says tend to bounce — i.e. it may systematically exit at local lows (the "sell at the noisy bottom" failure the MC gate exists to veto). Flag to empirical-auditor: System A and the MC veto are partially in tension by design.

---

### 6. Monte Carlo recovery gate — `run_monte_carlo` (`:979-1101`), `compute_regime_match_quality` (`:1611-1727`), gate in `compute_exit_confirmation` (`:457-518`)

**What it assumes.** (a) The 150 kNN-nearest historical days (matched on SPY return + rolling vol, `:1062-1073`) are a valid stand-in for "today's regime"; (b) bootstrapping 5000 draws from those days' portfolio returns gives a usable P(end-above-current); (c) if that probability ≥ 60% (`MC_SANITY_THRESHOLD` `:453`), vetoing the exit ("don't capitulate at a noisy low") helps more than it hurts. K=150 and 5000 paths have no calibration source (vision OQ-3/OQ-4 — confirmed: the constant comments at `:91-94` justify the *direction* of the tradeoff, not the specific values).

**Does it hold?** Bootstrap-from-analogues is a legitimate non-parametric technique (Efron 1979; historical-simulation VaR is the same family). The fatal structural weakness — stated in the vision (§3.6) and provable — is that **kNN regime-matching is least informative exactly when most needed**: in a true regime break, the "150 nearest" neighbors are merely the least-bad fits, so the veto is most likely to be confidently wrong precisely when an exit is most warranted. The `compute_regime_match_quality` guard (`:1611-1727`) is a *theoretically correct* mitigation: it computes a mean squared (z-scored Euclidean ≈ Mahalanobis) distance to the K neighbors and suppresses the veto when today is "unprecedented." The chi-squared threshold derivation (`:103-119`) is internally coherent (single-draw chi2(2)_{0.99}=9.21 applied conservatively to a mean-of-K statistic), and the conservatism direction (fire only on extreme breaks) is the right one for operator safety. The Mahalanobis-outlier framing (Mahalanobis 1936; Knorr-Ng 1998; Aggarwal 2017) is correctly cited.

**Verdict: PRACTICED-BUT-UNPROVEN (gate mechanism is sound; the veto's net value is unproven).** The simulation machinery and the regime-quality guard are theoretically defensible and the fail-safe (None → veto absent → stop still fires, `:501-511`, `:1014-1018`) is *correct and important* — an insufficient MC never disables protection. But the core bet — "history usually recovers from here, so don't sell" — is unproven net of the cases where it delays a needed exit, and the data is thinnest in the tail.
- **Empirical Evidence:** bootstrap/historical-simulation — `[Theoretical]`/`[Backtest]` (well-established); the *60% veto rule's net value* — `[Folklore]`.
- **Replication Status:** historical-simulation — replicated; the specific veto — No.
- **Regime Sensitivity:** Confidently-wrong in regime breaks (the guard mitigates but cannot eliminate this — its threshold is intentionally conservative, so it suppresses only the most extreme breaks). K=150 may be too large for tight regime locality in a fast-moving day.

---

### 7. CRRA-EU objective — `compute_crra_utility` (`:1552-1580`), `compute_crra_eu_objective` (`:1583-1608`); t-stat lives in `autotuner.compute_crra_eu_tstat`

**What it assumes.** A risk-averse investor's preferences are captured by CRRA utility `u(W) = (W^(1−γ)−1)/(1−γ)` (γ≈2), and the right thing to *maximize* in tuning is mean per-day utility, scored by a one-sample t-stat for "too big to be luck."

**Does it hold?** CRRA is the textbook utility for return-scale-invariant risk aversion (Pratt 1964; Arrow; Merton 1969; Samuelson 1969 — the code's L'Hopital γ→1=log-utility limit at `:77`/`:1577-1579` is exactly right). Using *expected utility* rather than raw return as the selection objective is theoretically *superior* to Sharpe for a risk-averse operator and directly encodes the "preserve capital, fear losses" north star. The `−1` numerator term is correctly flagged as mean-affecting (`:1559-1563`) — a real subtlety many implementations get wrong. The wealth floor `max(0.001, 1+r)` (`:1606`) correctly prevents `log(0)` blow-up on catastrophic returns.

**The theoretical problem is NOT the utility — it is the t-stat at T≈4.** The objective is sound; the *inference* on top of it is not. A one-sample t-stat needs the per-day utility series to be approximately i.i.d. and the sampling distribution of the mean to be near-normal. At the ~4 usable validation days the autotuner actually has (vision §4), neither holds — the code itself says so. CRRA utility of returns is *more* non-normal than returns (concave transform fattens the left tail), making the small-sample t-stat *worse*-behaved, not better. Yamai-Yoshiba's lesson (below) about tail estimators needing large samples applies in spirit: a risk-averse objective is most sensitive to the left tail, which is the least-sampled part.

**Verdict: PROVEN (the objective) / UNSOUND-AT-SCALE (the t-stat inference).** Splitting the verdict because the layer is two things. The CRRA-EU objective is the most theoretically defensible single choice in the entire engine. The t-statistic significance test wrapped around it at T≈4 is not statistically defensible, which the code admits in writing. This is a genuine "right objective, un-validatable test" situation.
- **Empirical Evidence:** CRRA utility — `[Theoretical]` (axiomatic, von Neumann-Morgenstern). The t-stat at T≈4 — `[UNSOUND]` by the source's own admission.
- **Replication Status:** CRRA — universally accepted; the small-sample t-stat — N/A (it's a known-invalid approximation).
- **Regime Sensitivity:** The objective is regime-agnostic (a preference, not a forecast); the *fitted parameters* it selects inherit the calibration-window regime.

---

### 8. CVaR diagnostic — `compute_cvar_5pct_general_distribution` (`:1272-1388`), `compute_portfolio_cvar` (`:1391-1549`), `CVaRAssessment` (`:140-187`)

**What it assumes.** 5% Expected Shortfall over the 150-neighbor pool is an informative tail-severity read for the operator. **Diagnostic only — never a trigger** (`__post_init__` forces `breach=False` when `cvar_pct is None`, `:162-169`; `breach=False` hard-coded in `compute_portfolio_cvar`, `:1530`).

**Does it hold?** The estimator is the **Rockafellar-Uryasev (2002) general-distribution formula with correct atom handling** (`:1379-1380`) and Acerbi-Tasche (2002) atom-contribution discipline — both correctly cited, and CVaR/ES is a *coherent* risk measure (Artzner et al. 1999) where VaR is not, so choosing ES over VaR is the theoretically superior choice. The stderr uses the **distinct genuine tail count** (~8 obs, not the 5000 resample count) — this is the H-2 binding and it is *exactly the right call*: using the resample count would understate stderr by ~√(5000/8)≈25× (`:1228-1230`), a category error the code explicitly avoids.

**The wall the project already hit, and whether it was right.** I verified **Yamai & Yoshiba (2002)** ([BIS/IMES](https://www.imes.boj.or.jp/research/papers/english/me20-1-4.pdf)): ES requires a **larger sample than VaR for equal accuracy, and the gap widens under fat tails** — precisely the regime where tail risk matters. At ~8 distinct tail observations the estimate has a very wide error bar. The project's decision-log walls were:
1. **EUT+CVaR live-trigger migration → REJECTED** (`project_eut_cvar_migration_council_verdict`): a live CVaR/ES exit trigger is structurally un-validatable at this data scale (needs ~1000s of tail-relevant obs; gets ~6-37). **This rejection is THEORETICALLY CORRECT** — it is a direct application of Yamai-Yoshiba + the backtesting-power literature. Confirmed sound.
2. **CVaR-divergence detector → REJECTED** (`project_cvar_divergence_validation_wall`): reframing as "validate a detector not an estimate" relocates the data wall onto the ~5-15 independent regime-shift events (correlated with the same scarce tail days), buying no new validation budget. **This rejection is also THEORETICALLY CORRECT** — the reframing does not escape the binding constraint (independent tail-relevant observation count), it just moves it. Confirmed sound.

**Verdict: PROVEN (as a diagnostic) / correctly-walled (as a trigger).** The estimator is textbook-correct and uses the superior coherent risk measure. The decision to keep it diagnostic-only is the theoretically correct response to the data wall. The residual concern (vision §6.1, assumption #8) is *operator-interpretation* risk — showing an ~8-obs estimate with a wide error bar to a lay operator who may over-read it — which is a UX/communication problem (ux-designer's surface), not a math-soundness defect.
- **Empirical Evidence:** R-U estimator — `[Theoretical]` (proven); the *informativeness at N≈8* — `[UNSOUND for action, marginal for diagnosis]`.
- **Replication Status:** R-U/Acerbi-Tasche — foundational, universally replicated. Informativeness at 8 obs — bounded by Yamai-Yoshiba.
- **Regime Sensitivity:** Worst-behaved under fat tails / regime breaks (Yamai-Yoshiba) — the regime where the operator would most want to trust it.

---

### 9. Six-layer / four-trigger priority resolution — `resolve_trigger_priority` (`:836-859`)

**What it assumes.** Priority order **VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop** ("fastest hard-cut first, slowest momentum-respecting last"), with co-firing flags recorded as telemetry.

**Verdict: PRACTICED-BUT-UNPROVEN.** Decomposing into independent transparent signals (rather than a learned "master signal") is a defensible interpretability choice and avoids over-fitting a fusion model on thin data — that judgment is sound. But the *specific ordering* — particularly TP ahead of Bleed Cut — has **no first-principles justification on file** (the cited "H2 acceptance criteria" doc is not on this branch, `:826-827`; confirmed absent). Priority only matters when ≥2 flags co-fire in the same cycle, and since each is a confirmed multi-tick signal, the practical impact is bounded — but it is an un-anchored choice.
- **Empirical Evidence:** `[Folklore]` (the ordering); the decomposition choice is `[Theoretical]`-defensible.
- **Replication Status:** No.
- **Regime Sensitivity:** Matters only on co-fire cycles; low blast radius.

---

## Engaging the central question

> *Does this family of techniques have any theoretical basis for producing the user's north star (halve drawdowns + capture upside), or is it theoretically a coin flip?*

**It is not a coin flip — but it is not free alpha either. The honest answer is asymmetric:**

**The "halve drawdowns" half has a theoretical basis.** A trailing-stop / breakeven-lock overlay *mechanically* truncates the left tail of intraday outcomes. Under any return distribution, converting a position to cash at a drawdown threshold bounds the loss from that point. Drawdown reduction does not require any regime assumption — it is a near-tautology of the mechanism (you cannot lose what you've exited). So the protective half of the north star is **theoretically supported**, at the known cost of (a) whipsaw exits and (b) realized transaction/slippage costs.

**The "capture upside" / "net-recover more than it costs" half is regime-conditional and UNPROVEN for this universe.** This is the load-bearing claim and the literature the project itself cites is unambiguous:
- **Kaminski & Lo (2014)**, *When Do Stop-Loss Rules Stop Losses?* (*J. Financial Markets* 18, 234-254; [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338)): I verified the central result — **under a random walk, a stop-loss ALWAYS reduces expected return; only under momentum / positive serial correlation can a stop ADD value (the "stopping premium").** Whether Planet Stopper's specific rotating-ETF symphonies live in the favorable (momentum/serially-correlated) regime at the *intraday* horizon is **not established anywhere in the codebase or docs.** That is the single empirical question on which the entire value proposition turns, and it is unanswered. (Hand to empirical-auditor.)

**So the theoretical bottom line:**
1. As a **drawdown-control overlay**, the family is theoretically sound and the fail-safe engineering is genuinely good (None-sentinels never disable protection — verified at `:162-169`, `:501-511`, `:1014-1018`, `:220-228`).
2. As an **alpha / "Guard Alpha is positive on average" engine**, it is theoretically UNPROVEN and conditional on a momentum regime the project has not demonstrated holds for its universe. By Kaminski-Lo it could be value-*subtracting* if the symphonies are closer to random-walk intraday.
3. The most sophisticated pieces (live tail-risk action) were **correctly identified as un-validatable and walled off** — both walls (EUT+CVaR trigger, CVaR-divergence) are theoretically correct applications of Yamai-Yoshiba and the backtesting-power literature. Do not re-litigate; they were right.

**It is a competently-built risk overlay whose protective value is theoretically real and whose alpha value is an unproven regime bet — not a coin flip, but not a sure thing, and the project is unusually honest about which is which.**

---

## Citation-tension flags (surfaced per HARD SCOPE rule 4, not silently resolved)

1. **Danielsson-Zigrand (2003) cited as THEORY support for the √-time decay curve (`:241-242`)** — but that paper's actual finding is that the √-time rule is *biased* under serial correlation. The i.i.d. premise of the decay curve contradicts the momentum premise the rest of the system relies on. Both citations stand; the tension is real.
2. **Leung-Zhang (2019) cited for the VWAP regime-switch gate (`:759-761`)** — proves trailing-stop optimality under a *mean-reverting* (exp-OU) model, not the trend/momentum regime the overlay targets. The code's own honest-flag (`:769-773`) half-acknowledges this.
3. **VWAP-below-as-exit (System A/B) vs. the academic VWAP edge** — the documented statistical edge in below-VWAP price is *mean-reversion (bounce)*, the opposite direction from the breakdown/exit reading the engine uses. Conflicting evidence surfaced, not adjudicated.

## Open questions (logged, not investigated — out of scope)
- Are the operator's actual symphonies momentum-regime or random-walk-regime at the intraday horizon? (→ empirical-auditor; this is THE question.)
- Net-of-cost Guard Alpha distribution across symphonies and forward regimes (→ empirical-auditor).
- Look-back consistency of `calculate_20d_vol` when symphony holdings rotated within the 20-day window (→ mathimpl-auditor).
- Practical co-fire frequency of the four triggers, to size the priority-order blast radius (→ runtime-auditor / empirical-auditor).

## Sources
- [Kaminski & Lo (2014), *When Do Stop-Loss Rules Stop Losses?*, J. Financial Markets 18:234-254 (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338) · [MIT open-access PDF](https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf)
- [Danielsson & Zigrand (2003), *On time-scaling of risk and the square-root-of-time rule*, LSE FMG DP-439](https://eprints.lse.ac.uk/24827/1/dp439.pdf)
- [Leung & Zhang (2019), *Optimal Trading with a Trailing Stop*, Applied Math & Optimization 80:669-698, DOI 10.1007/s00245-019-09559-0](https://link.springer.com/article/10.1007/s00245-019-09559-0)
- [Yamai & Yoshiba (2002), *Comparative Analyses of Expected Shortfall and Value-at-Risk*, IMES/BOJ Monetary & Economic Studies 20(1):87-121](https://www.imes.boj.or.jp/research/papers/english/me20-1-4.pdf)
- [Harvey & Liu (2015), *Backtesting* (Duke / SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489) · [Duke PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF)
- VWAP mean-reversion synthesis: [QuantifiedStrategies, VWAP backtest & evaluation](https://www.quantifiedstrategies.com/volume-weighted-average-price/) `[Tier 4 — corroborates direction-of-signal only]`
- Foundational (not re-fetched this pass, standard references): Rockafellar & Uryasev (2002) *Optimization of CVaR*; Acerbi & Tasche (2002) *On the coherence of expected shortfall*; Artzner et al. (1999) *Coherent measures of risk*; Cont (2001) *Empirical properties of asset returns: stylized facts*; Moreira & Muir (2017) *Volatility-Managed Portfolios*, J. Finance; Merton (1969)/Samuelson (1969) CRRA/log-utility.
