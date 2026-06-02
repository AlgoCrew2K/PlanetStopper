<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Vision reconstruction used as anchor for the soundness audit; high-value provenance for future work on the adaptive system. Conclusion recorded in DECISIONS.md and memory/project_adaptive_exit_direction.md. -->
# Planet Stopper — Reconstructed Vision (Intake for Soundness + Empirical-Validity Audit)

> **What this document is.** A plain-language reconstruction of *what* the Planet Stopper trading risk engine is trying to do and *how* its math attempts to do it. Written for a non-quant reader. Every term of art is defined the first time it appears. Where I am inferring intent rather than reading it stated, I write **(inferred)**. Concrete mechanism claims cite `file:line`.
>
> **Source caveat.** The project's own `README.md` is itself a recently-written "vision audit" reconstruction. I treated it as a *claim to be checked against code*, not as ground truth. Where I verified a claim directly in `math_engine.py` / `autotuner.py`, I cite the code. Where I am relaying a README assertion I did not independently confirm (mostly in `alpha_bot_execution.py`, which I did not read directly this pass), I mark it **(per README, unverified here)**.
>
> **This is an intake document.** It anchors a later soundness + empirical-validity audit. It does not itself certify any claim as sound.

---

## 1. The problem being solved

**The user's situation.** The operator runs one or more **symphonies** on a platform called **Composer.trade**. A "symphony" is Composer's name for a rule-based strategy that rotates among ETFs (baskets of stocks) according to rules the operator wrote or licensed. Composer decides *what to hold and how much*. Crucially, Composer does **not** actively manage intraday drawdowns — once it's in a position for the day, it generally rides it to the close.

**The pain.** A symphony can be up nicely at 11:00 a.m. and give it all back (or worse) by 3:30 p.m., and Composer will not step in to protect that intraday gain. The operator wants something watching each symphony minute-by-minute that **exits to cash when the day's gain is at risk** — a disciplined "get out now" overlay.

**What Planet Stopper is.** A Python daemon that, every minute during US market hours, pulls each symphony's current holdings and price data (from a market-data provider called **Alpaca**), computes how the symphony is doing today, and decides whether to **liquidate it to cash** through Composer's API. It is purely an **exit** tool. It never opens positions, never picks symbols, never sizes anything (README §1, §8).

**"Guard Alpha."** This is the product's headline metric and value claim. "Alpha" in finance loosely means "value added beyond just holding." **Guard Alpha is the dollar difference between what the operator got by exiting early (when Planet Stopper fired) versus what they would have gotten by holding to the close** (README §2 "Exit decisions"; surfaced in the Discord post-mortem and the `/perf-snapshot` skill). It is the system's answer to "did stopping out actually save me money versus doing nothing?"

**A trailing stop**, the core tool, is a sell rule that sits a certain distance below the highest point the position has reached today. As the position climbs, the stop climbs with it (it "trails"). It never moves down on its own. When the price falls back down to the stop line, you sell. The whole engine is an elaboration on this one idea.

---

## 2. The value proposition / north star

**Success, in the operator's words (inferred from README §1 "Should I run this?" and §4):** *"I keep running my Composer symphonies exactly as I do today, and an attentive robot watches each one and pulls it to cash when the day is turning against me — saving more than it costs me in missed rebounds, and never blowing up my account on a bad day."*

The system makes several explicit promises about what it will and will not do:

- **It will give "institutional-grade exit discipline" without the operator coding it** (project CLAUDE.md "Purpose"; README masthead).
- **It will fail safe, never fail open.** If any supporting signal is unavailable, the protective stop still fires; the bot never holds into a crash because a fancy signal was missing (README §4.7; `math_engine.py:508`, `math_engine.py:162-169`).
- **It will not fool itself with overfitting.** The parameter-tuning machinery is explicitly built to *refuse to deploy* parameters it cannot statistically distinguish from luck (README §3.5, §6; `autotuner.py:415-430`).
- **It is an overlay, not autopilot.** It expects an attentive operator reading a dashboard and a daily Discord summary; it is "exit discipline," not a hands-off product (README §1, §8).
- **It optimizes risk-adjusted experience, not raw return** — the tuner maximizes a *risk-averse* utility, so the north star is capital preservation, not return maximization (README §3.4).

The deepest claim, and the one the empirical audit must test hardest, is: **early exits recover more value than they cost.** That a stop, on average and net of whipsaws, beats holding. This is the entire reason to run the thing, and (as §5 below shows) the literature the project itself cites says this is *regime-dependent* — true under momentum, false under random-walk noise.

---

## 3. The mechanism, layer by layer

Planet Stopper's documentation talks about "six math layers" and "four exit triggers." The reconciliation (README §4.2): there are **six upstream computations** that feed **four exit-decision flags**. Four of the computations collapse into one flag (Trailing Stop), the two VWAP computations split into two flags, and Monte Carlo is a *gate* (a veto/permission check) on two of the flags rather than a flag of its own.

Below, each computation in `math_engine.py`, what it computes, and why it supposedly helps.

### 3.1 Volatility scaling — how wide should the stop be?
**Computes:** the 20-day **realized volatility** of the symphony — a number describing how much the symphony's daily return typically bounces around (`calculate_20d_vol`, `math_engine.py:1104-1136`; a 14-day ATR variant, `calculate_14d_atr_pct`, `:1139-1193`). **"Volatility"** = the standard deviation of recent returns; high volatility = noisy/jumpy.
**Why it helps:** a noisy symphony needs a *wider* stop so ordinary jitter doesn't trip an exit; a calm symphony can use a *tighter* stop. The active stop distance is `max(vol × multiplier, floor)` (`compute_active_trailing_stop`, `:328-372`). The 20-day window is the industry-standard RiskMetrics horizon (`:64`). **This is the most mainstream, least controversial layer.**

### 3.2 Time-squeeze decay — tighten the stop as the day runs out
**Computes:** a multiplier that shrinks from 1.5× at the open to 0.5× at the close, following the curve `f(t) = 1 − √(1 − t)`, where `t` is the fraction of the trading session elapsed (`compute_time_squeeze_decay`, `:294-325`; constants `:231-250`).
**Why it helps:** with less of the day left, there is less time for a dip to recover, so it makes sense to protect gains more aggressively late in the session. The project re-derived this curve from the "square-root-of-time" rule for how return-uncertainty shrinks as the remaining window shrinks — they claim it has **zero free (fitted) parameters**, which is why they label it "THEORY" rather than a guess (`:231-244`, citing Danielsson & Zigrand 2003). **Note:** the README §3.7 in places still references an older `log10(1 + 9t)` heuristic and lists a "redrive" as pending — the code itself is the newer √-time form, so this is a stale-doc tension, not a code gap.

### 3.3 Parabolic ratchet (PARA-ARM) — clamp down after a fast spike
**Computes:** `velocity = current_return − prev_return`; if velocity exceeds a threshold and the ratchet isn't already armed, it arms (`compute_para_arm_decision`, `:268-291`). Once armed, the stop distance is multiplied by a tightening factor (`para_armed` branch in `compute_active_trailing_stop`, `:370-371`).
**Why it helps:** if a symphony spikes up fast, the move is often unsustainable; tightening the stop "locks in" the spike before it reverses. **Honest-gap flag:** the velocity threshold (default 2.0 pp) and squeeze multiplier have **no published calibration source** — they are practitioner heuristics (README §3.7, OQ-5). There is also an unresolved question about whether the ratchet can spuriously auto-arm on the *second tick* of a session (README §3.7 "PARA-ARM day-boundary behavior," OQ-5).

### 3.4 Breakeven lock — once you've earned it, don't give it back
**Computes:** counts consecutive ticks the symphony holds at/above a volatility-derived activation threshold; after 5 such ticks (`HWM_HOLD_TICKS_THRESHOLD`, `:260`) it latches `breakeven_locked = True` **permanently for that position** and floors the stop at 0.0 — i.e., "no worse than break-even" (`compute_breakeven_update`, `:375-448`).
**Why it helps:** once a position has clearly established a gain, the operator should not be allowed to round-trip back into a loss. The lock is **one-way** (latching) — it never un-locks within a position. **(inferred)** This is the only true upward "ratchet" in the resolved stop level (the docstring at `:417-421` says so explicitly).

### 3.5 VWAP signals — is the symphony breaking down relative to its average price?
**VWAP** = Volume-Weighted Average Price, the average price paid across the day weighted by how much traded at each price. Trading below VWAP is a common "the buyers have lost control today" signal.
**Computes:** an allocation-weighted deviation of each holding's last price from its VWAP (`compute_vwap_signals`, `:590-642`), then runs a two-system state machine (`compute_vwap_breakdown_update`, `:684-789`):
- **System A — VWAP Breakdown** (profit-protection): only active once the high-water mark has banked enough gain (`safe_hwm >= vwap_cross_hwm_pct`); fires after 3 confirming ticks. The "only above a gain threshold" structure is a **regime switch** the project anchors to optimal-stopping theory (Leung & Zhang 2019; Peskir 1998) — but candidly flags as an *interpretive extension*, not a proven theorem (`:751-773`).
- **System B — VWAP Bleed Cut**: fires when the return drops below a dynamic, volatility-scaled negative threshold (`compute_vwap_bleed_arm_threshold`, `:650-676`) for enough ticks. Catches a *slow* erosion that a sharp-cross detector would miss.
**Why it helps:** a sharp VWAP break and a slow bleed are different failure modes; two detectors catch both.

### 3.6 Monte Carlo gate — "is today actually bad, or just noisy?"
**Monte Carlo** = simulating many random outcomes to estimate a probability. **Computes:** finds the **150 historically-most-similar days** to today (a *k-nearest-neighbors* match on SPY's return and rolling volatility — "kNN" means "look up the closest matches"), then bootstraps 5,000 random draws from those days' portfolio returns and computes **the probability that the day ends *above* the current return** (`run_monte_carlo`, `:979-1101`).
**Why it helps:** before capitulating, the bot asks "in regimes like today, do we usually recover from here?" If the probability of beating the current level is high (≥ 60%, `MC_SANITY_THRESHOLD`, `:453`), it **vetoes the exit** — "don't sell at a noisy local low" (`compute_exit_confirmation`, `:457-518`). A symmetric mechanism gates Take-Profit (`compute_tp_confirmation`, `:525-587`).
**Critical fail-safe:** if there isn't enough history (fewer than ~39 raw days; see §5 below), MC returns `None`, and the veto is treated as **absent** — the protective stop fires anyway (`:501-511`). An insufficient MC must never *disable* protection.
**Honest-gap flags:** the neighbor count K=150 and path count 5,000 have **no calibration source** (README OQ-3, OQ-4); and the README itself concedes the gate "is least informative when most needed" — in a true regime break, the 150 "nearest" neighbors are all poor fits (README §3.3 soundness verdict). A *regime-match-quality guard* (`compute_regime_match_quality`, `:1611-1727`) exists to detect exactly this and suppress the MC veto when today is "unprecedented," but per the inline notes it is a recently-added guard whose threshold is intentionally conservative.

### 3.7 CVaR diagnostic — how bad is the bad case, on average?
**VaR (Value-at-Risk)** at 5% answers "how bad is a 1-in-20 day, roughly?" **CVaR (Conditional VaR / Expected Shortfall)** answers the better question "*when* that 1-in-20 day happens, how bad is it **on average**?" **Computes:** CVaR at the 5% tail over the same 150-neighbor regime-matched pool, using the Rockafellar-Uryasev general-distribution estimator with correct handling of the boundary observation (`compute_cvar_5pct_general_distribution`, `:1272-1388`; wrapper `compute_portfolio_cvar`, `:1391-1549`).
**Why it (supposedly) helps:** gives the operator a regime-aware read on tail severity.
**The load-bearing caveat the system itself shouts:** CVaR is **diagnostic only — never a trigger** (`CVaRAssessment.__post_init__` forces `breach=False` whenever the estimate is absent, `:162-169`; README §4.4). And the tail sample is tiny: 5% of ~150 neighbors ≈ **8 distinct tail observations**, so the estimate has a wide error bar (README §3.2 thinness caveat). This is the single most-litigated number in the project — see §6.

### 3.8 The six-layer / four-trigger exit decision
Each cycle produces four boolean flags — `is_vwap_broken`, `is_tp_hit`, `is_vwap_bleed_broken`, `is_trailing_stop_hit` — and `resolve_trigger_priority` (`:836-859`) picks **one** winner in a fixed priority order:

> **VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop**

The stated rationale (README §4.2) is "**fastest hard-cut first, slowest momentum-respecting cut last.**" Every co-firing flag is recorded as telemetry alongside the winner, so the operator can tell a high-conviction "all four fired" exit from a lone noise spike. **Honest gap:** the specific placement of Take-Profit *ahead of* Bleed Cut has **no first-principles justification on file** — the in-code comment cites an "H2 acceptance criteria" document that isn't on this branch (README OQ-1; `:826-833`). The project deliberately rejected combining the four into a single learned "master signal," preferring transparent independent signals (README §4.2).

---

## 4. The optimization story (the autotuner)

Every night after market close, for **each symphony**, the autotuner re-chooses the engine's tunable parameters by replaying 125 trading days of history (README §6; `autotuner.py` `run_autotuner`).

**What it does, in plain steps:**
1. **Split history** 60% train / 20% validation / 20% "frozen-eval," with **purge (20 days) + embargo (1 day)** buffers at the boundaries so information from the training side can't leak into the test side (`autotuner.py:360-377` and README §3.5). "Frozen-eval" = a hold-out slice scored exactly **once**, after the winner is chosen, as the honest report card.
2. **Run 500 trials** of random-ish parameter sets via Optuna's TPE sampler (which concentrates the search on promising regions — `MAX_OPTUNA_TRIALS = 500`, `autotuner.py:437`).
3. **Score each trial** not by raw return but by a **CRRA expected-utility t-statistic** (below).
4. **Apply the BHY overfitting haircut** (below) and **only deploy if the winner clears the raised bar**; otherwise keep yesterday's parameters.

**What it optimizes — the CRRA-EU objective.** **CRRA** = "Constant Relative Risk Aversion," a textbook utility curve for an investor who fears losses more than they enjoy equal-sized gains. Each daily return `r` is turned into a "wealth ratio" `W = max(0.001, 1 + r)` and then into a utility score `U = (W^(1−γ) − 1)/(1−γ)` (or `ln(W)` when γ=1) — concave, so big losses hurt disproportionately (`compute_crra_utility` `:1552-1580`, `compute_crra_eu_objective` `:1583-1608`). **γ (gamma)** is the risk-aversion dial; the default lives near γ=2 ("moderately risk-averse retail"). The trial score is the **one-sample t-statistic** `mean(U) / (sd(U)/√T)` (`compute_crra_eu_tstat`, `autotuner.py:520-553`) — a "is this signal too big to be luck?" number. The project is explicit that this must be a genuine t-stat, not the older `effect_size × √T` form that omits the standard deviation (the "H-6 category error," `autotuner.py:529-534`).

**The overfitting haircut — the crown jewel.** If you try 500 parameter sets and keep the best, the best one looks better than it deserves *by luck alone* (the "multiple-testing problem"). The **BHY (Benjamini-Hochberg-Yekutieli) haircut** raises the bar in proportion to how many trials were run, using the Yekutieli factor `c(N) = Σ 1/j` for dependent tests (`autotuner.py:415-466`). If the best trial can't clear the raised bar, **nothing deploys.** A tripwire called `N_effective = N_optuna + S` (additive) adds in any parameters a human hand-selected offline (`S`), so you can't game the test by pre-filtering by hand (README §3.5).

**NN1 spec-freeze.** Every frozen parameter carries a `freeze_discipline` tag explaining *why* it has its value. Six honest reasons are allowed (THEORY, MANDATE, STYLIZED_FACT, POLITIS_WHITE, CADENCE, CALIBRATION); one — `BACKTEST_SELECTION` ("I picked it because the backtest liked it") — is **banned** and makes the autotuner refuse to run (README §3.6). γ, the utility family, and the wealth-argument formula are THEORY-frozen and **never** tuned by Optuna (README §6 "What stays frozen").

**The most important number the autotuner reveals about itself:** after the 60/20/20 split and the purge/embargo buffers, the validation window shrinks to **~4 usable days** (`_OOS_USABLE_VALIDATION_DAYS_EXPECTED = int(125×0.2) − 20 − 1 = 4`, `autotuner.py:360-377`). The code itself states this is "too thin for the normal-CDF approximation ... to be defensible" and that BHY's correction "does NOT substitute for thin per-trial sample length." **This is a self-flagged statistical-power wall, in the code, in writing.**

---

## 5. Load-bearing assumptions (the things that MUST be true)

These are the beliefs the whole vision rests on. Each is stated skeptically.

1. **Early exits net-recover value versus holding.** The product's entire reason to exist. The project's *own* cited reference (Kaminski & Lo 2014, README §3.3) says stops add value under **momentum** regimes and *subtract* value under **random-walk** regimes. So this assumption is conditional, not universal — and whether the operator's symphonies live in the favorable regime is unproven.

2. **Recent volatility forecasts near-term volatility.** Volatility scaling (§3.1) assumes the last 20 days' noisiness predicts today's. Generally defensible (volatility clusters), but it breaks exactly at regime transitions — when protection matters most.

3. **The 150 nearest historical days are a valid stand-in for "today's regime."** The MC gate and CVaR both assume kNN-matched history resembles the live regime. In a genuine break, the "nearest" neighbors are merely the *least-bad fits* — the assumption is weakest precisely when the bot is being asked to act (README §3.3 soundness verdict).

4. **125 trading days is a representative, sufficient calibration window.** The autotuner trains on ~6 months. The code concedes the resulting validation fold is ~4 usable days (`autotuner.py:360-377`) and the README concedes 125 days is "short by published walk-forward standards" (§3.5). If the regime in those 125 days isn't representative of the forward regime, the tuned parameters are tuned to the wrong world.

5. **Daily returns over a fold behave well enough for a one-sample t-stat.** The CRRA-EU score (§4) assumes the per-day utility series is approximately suited to a normal-CDF p-value. With T≈4, the code itself says this is not defensible (`autotuner.py:362-367`).

6. **The MC sanity veto's "don't capitulate" rule helps more than it hurts.** Blocking an exit because history "usually recovers from here" is a bet that this time resembles history. When it doesn't, the veto delays a needed exit. The fail-safe floor (the trailing stop fires regardless when MC is `None`) bounds this risk only in the *insufficient-data* case, not in the *confidently-wrong* case.

7. **The four triggers and their priority order are the right decomposition.** Assumes these four signals catch the relevant failure modes and that the priority order is correct. The TP-before-Bleed ordering has no on-file justification (README OQ-1).

8. **CVaR is genuinely informative as a diagnostic at ~8 tail observations.** Even kept off the trigger path, an ~8-observation tail estimate with a wide error bar is being shown to a non-expert operator as a decision aid. The assumption is that the operator interprets it as "a discussion prompt, not a forecast" (README §3.2) — a strong assumption about operator behavior.

9. **The PARA-ARM and VWAP thresholds, though un-derived, don't actively hurt.** Several thresholds (§3.3, §3.5) are practitioner heuristics with no calibration source and are *tuned on the same thin 125-day window*, exposing them to the very overfitting the BHY haircut is meant to police.

---

## 6. Visible tensions / pipe-dream risks

The codebase and decision log are unusually candid about their own limits. The recurring theme: **the math is mostly sound in form; the data is too thin to prove it works.** Both can be true at once, and the project says so repeatedly.

### 6.1 The two walls the project already hit (from the decision log)

**The rejected EUT+CVaR migration (`project_eut_cvar_migration_council_verdict`).** A "decision-science council" evaluated replacing the heuristic exit core with a formal Expected-Utility + CVaR decision engine — and **did not recommend it**. The verdict was "harden, don't migrate": keep the heuristic stack, freeze the CRRA objective, ship CVaR only as a *diagnostic*. The binding reason: **a live CVaR trigger is structurally un-validatable at this data scale.** Properly back-testing a VaR/ES exit rule needs ~1,000 tail-relevant observations; a 125-day fold yields ~6 tail days, and even 3 years yields ~37 (Yamai-Yoshiba). You cannot prove the trigger works with the data you can ever realistically have. *Do not re-litigate* is stamped on this entry.

**The rejected CVaR-divergence detector (`project_cvar_divergence_validation_wall`).** A proposed escape hatch — compare a short-window CVaR against a long-window CVaR and trade the *divergence* as a regime-shift detector, "validate a detector not an estimate" — was also **rejected**. The finding: reframing as detection doesn't escape the data wall, it *relocates* it onto the count of **independent regime-shift events** (~5-15 over 3 years), which are themselves correlated with the same scarce tail days. No new validation budget is bought. It survives only as a passive operator diagnostic with the signed-divergence number *forbidden* from ever being persisted or displayed (README §7.3 "Hard wall"; `advisors/divergence_explainer.py:14-19`).

**Why these matter for the audit:** they are the project's own admission that its most sophisticated risk machinery (tail-risk-driven action) **cannot be empirically validated at the data scale the product will ever operate at.** This is the central pipe-dream risk and it is already acknowledged in writing.

### 6.2 The statistical-thinness thread (everywhere)
- Walk-forward validation fold ≈ **4 usable days** (`autotuner.py:360-377`) — code says the per-trial t-stat is "not defensible" at that length.
- CVaR tail ≈ **8 observations** (README §3.2) — wide error bar, shown to a lay operator.
- MC kNN gate is "least informative when most needed" (README §3.3).
- 125-day calibration window is "short by published standards; Pardo recommends 5-10 folds, Planet Stopper uses 1" (README §3.5).

The BHY haircut is the genuine bright spot — it correctly polices *selection* bias across trials — but the code is explicit that it **cannot fix thin per-trial sample length** (`autotuner.py:364-367`). So even the strongest piece of math is bounded by the data wall.

### 6.3 Un-anchored heuristics that are nonetheless live trade signals
The PARA-ARM velocity threshold + squeeze multiplier (OQ-5), the VWAP thresholds (OQ-6), the 15-minute open grace window (OQ-7), K=150 (OQ-3), 5,000 paths (OQ-4), and the Sortino sentinel magnitude (OQ-10) all **have no published or calibrated provenance** and are surfaced to the operator as live signals. The README is honest that the parabolic ratchet "lives or dies by empirical evaluation that the 125-day calibration window cannot deliver with high confidence" (§3.7). These are tuned on the same thin window, so they sit inside the overfitting blast radius the rest of the design works hard to contain.

### 6.4 Unproven-by-construction value claim
**"Guard Alpha is positive on average"** — the headline promise — depends on assumption #1 (early exits net-recover value), which the cited literature makes regime-dependent. There is no on-file evidence that, across the operator's actual symphonies and forward regimes, the early exits beat holding net of whipsaw costs. The recommended evaluation path is the operator running two weeks of paper mode and judging fit themselves (README §1) — i.e., **the burden of proving value is pushed to the operator**, because the engine cannot prove it from history at this data scale.

### 6.5 Doc-vs-code drift to watch (minor, for the auditor)
The README in places (§3.7, §12 "Phase 1.5 M3") still references an older `log10(1+9t)` time-squeeze curve and lists its re-derivation as *pending*, while the code already implements the newer √-time form with "M3 redrive shipped" (`math_engine.py:231-245`, `:294-325`). The README architecture table also says "24 migrations (001-024)" (§10) while project memory and the project CLAUDE.md say 25 (through migration 025). These are stale-doc artifacts, not engine defects, but they mean **the README cannot be trusted as fully synchronized with the code** — the audit should verify against code, not prose.

---

## Appendix — what I read vs. inferred

- **Read directly and cited from code:** all of `math_engine.py` (1727 lines), the objective/haircut/NN1 region of `autotuner.py` (~lines 360-560 plus constants), project `CLAUDE.md`, project `README.md` (772 lines), and the decision-log memory files (`project_eut_cvar_migration_council_verdict`, `project_cvar_divergence_validation_wall`, `project_mc_eligible_pool_vs_raw_day_boundary`, `project_mc_sentinel_consumer_blast_radius`, `project_sprint_3_complete`, `MEMORY.md` index).
- **Relayed from README, not independently verified this pass:** the per-cycle wiring in `alpha_bot_execution.py` (the :00 scheduler, HWM update, the order the six layers are called, the queue/drain, Discord/Guard-Alpha reporting). All such claims are marked **(per README, unverified here)** in context.
- **(inferred)** is marked inline wherever I reconstructed intent rather than reading it stated.
