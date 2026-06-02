<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Empirical-validity findings: Guard Alpha day-clustered t=1.52 NS; intraday lag-1 AC = -0.036 (mean-reverting); SPY/TQQQ blocked. Recorded in memory/project_adaptive_exit_direction.md. -->
# Pillar 3 — Empirical Validity vs Baselines

**Auditor:** empirical-auditor (Agent Team `audit-soundness`)
**Worktree HEAD:** `8586ab2cf06ecf5fcc1dd8db91c76e55aa4329be`
**Run timestamp (UTC):** 2026-05-30T16:01Z
**Scope:** Measure, risk-adjusted, the north-star ("early exits recover more value than they cost; Guard Alpha is positive on average; drawdowns roughly halve") against three baselines (B&H the same symphonies, SPY, TQQQ) at per-symphony and portfolio level — then deliver the honest-broker verdict on whether the empirical bar is reachable at this data scale.

---

## 0. TL;DR — Honest-Broker Verdict

**The empirical bar ("proven to beat baselines, risk-adjusted") is NOT reachable at the data scale this product operates at, and is not reached today.** Two independent walls force this:

1. **Live operating data is ~6 trading days, not 125, not 3 years.** The Guard-Alpha-measurable record (`shadow_history`) spans only **2026-05-18 → 2026-05-29 (6 distinct trading days)**. The decision log (`exit_triggers`) holds **23 rows total, one of which is a synthetic seed**. There is no multi-month live track record to measure.
2. **The naive "it works" signal is a statistical artifact of pseudo-replication.** I *can* compute a Guard Alpha, and at the episode level it looks positive and "significant" (mean **+0.47pp**, one-sample t ≈ **2.25–2.73**, p<0.05). But the 22 episodes are **not independent** — they cluster into only **5–6 trading days** sharing a common market regime. Clustering correctly by trading day (the valid independent unit) **collapses the signal to t ≈ 1.52, NOT significant** (crit ≈ 2.78 at df=4). The apparent significance is the same thin-sample illusion the project's own autotuner warns about ("BHY correction does NOT substitute for thin per-trial sample length", `autotuner.py:364-367`).

**Guard Alpha is directionally positive and plausibly real in mechanism, but it is statistically unproven and structurally unprovable at this data scale.** This corroborates — with live data — the project's own written admissions (README §1, §3.5; `project_eut_cvar_migration_council_verdict`).

---

## 1. What I Could and Could Not Measure (data-access ledger)

| Baseline / metric | Status | Why |
|---|---|---|
| **Guard Alpha (exit vs hold-to-close), per symphony-day** | ✅ COMPUTED | `shadow_history.shadow_return` (frozen at exit) vs `current_return` (counterfactual hold) — fully populated, 0 NULLs |
| **B&H the same symphonies (regime / autocorrelation)** | ✅ COMPUTED | 125-day daily-close returns reconstructed from `cache/synthetic_history_v2_*.json` (keyed by symphony_id) |
| **Drawdown-protection (downside avoided)** | ✅ COMPUTED | post-trigger hold-path minimum vs frozen exit level |
| **Autotuner OOS report card (frozen-eval Sharpe, deflated Sharpe)** | ✅ READ | `autotune_runs` (1,656 rows) |
| **SPY baseline (raw + risk-adjusted)** | ❌ BLOCKED | No SPY price series in cache; requires live Alpaca. **No Alpaca creds in env** (`ALPACA*`/`APCA*` keys absent); background agent auto-denies. **Did NOT fabricate.** |
| **TQQQ baseline (raw + risk-adjusted)** | ❌ BLOCKED | Same as SPY. TQQQ is 3× leveraged — comparison would have to be risk-adjusted anyway; without the series it is uncomputable. **Did NOT fabricate.** |
| **Sharpe/Sortino of the live strategy** | ❌ NOT MEANINGFUL | 6 trading days of live data is far too short for a return-series Sharpe; computing one would be misleading. The autotuner's own `frozen_eval_sharpe` is a degenerate sentinel (see §4). |

**Blocker reported to team-lead:** SPY/TQQQ benchmarks need live Alpaca credentials unavailable in this env. The B&H-same-symphonies baseline (the most decision-relevant one) WAS computable from the cache and is reported below.

---

## 2. Guard Alpha — the actual live measurement

**Primitive (verified against `exit_triggers`):** before a trigger, `shadow_return == current_return`. At the trigger tick, `shadow_return` **freezes** at the exit-locked return (e.g. episode iaSOOUsm 2026-05-18 froze at **+1.68%**, exactly matching `exit_triggers.id=1 at_return=1.68`); `current_return` continues tracking what holding-to-close would have earned. Therefore:

```
guard_alpha (pp) = exit_return (shadow, frozen) − hold_to_close_return (current, EOD)
                 = + when exiting beat holding ;  − when exiting whipsawed (left money on the table)
```

**22 symphony-day episodes** (11 symphonies × the days each triggered). One episode (iaSOOUsm 2026-05-20, exit −5.50) is the **synthetic seed** `seed-early-exit-001`, flagged and reported both with and without it.

| Metric | Value (seed incl.) | Value (seed excl. — honest) |
|---|---|---|
| n episodes | 22 | 21 |
| **mean Guard Alpha** | **+0.47 pp** | **+0.55 pp** |
| median | +0.205 pp | +0.21 pp |
| stdev | 0.96–0.98 pp | 0.93 pp |
| % positive (exit helped) | **59.1%** | ~62% |
| % negative (whipsaw — exit hurt) | **40.9%** | ~38% |
| best / worst episode | +2.69 / −1.25 pp | +2.69 / −0.71 pp |
| one-sample t (mean vs 0) | 2.25 | 2.73 |

**Reading:** Guard Alpha is **directionally positive** — exiting beat holding ~60% of the time, by roughly half a percentage point on average. But the whipsaw tail is real: **~40% of exits gave back upside** (e.g. nOyb55RM 2026-05-19: exited at +0.32 while hold-to-close was +1.03, a −0.71pp whipsaw). This is exactly the regime-dependent behavior Kaminski-Lo predicts (README §3.3).

---

## 3. The independence problem (why the t-stat is illusory) — the load-bearing finding

The episode-level t-stat (2.25–2.73) **assumes 22 independent observations**. They are not. They span **only 5–6 distinct trading days**, and 11 symphonies that share market beta. Guard Alpha is dominated by **day-level market regime** (a trending-up day helps every held position; a reversal day helps every exit). The valid independent unit is therefore the **trading day**, not the episode.

**Day-clustered Guard Alpha (seed excluded):**

| Trading day | mean GA (pp) |
|---|---|
| 2026-05-18 | +0.018 |
| 2026-05-19 | +0.703 |
| 2026-05-20 | +0.330 |
| 2026-05-21 | +2.475 |
| 2026-05-22 | −0.010 |

- n_days = 5, mean = **+0.70 pp**, sd = 1.03, **t = 1.52**
- critical t (α=0.05, two-sided, df=4) ≈ **2.776**
- **NOT statistically significant.** One good day (05-21, +2.48) drives most of the apparent edge.

> The naive episode-level significance is **pseudo-replication**: counting correlated minute/episode observations as if independent. Correcting for it erases the significance. This is the live-data analogue of the autotuner's self-flagged "~4 usable validation days" wall (`autotuner.py:360-377`).

---

## 4. Autotuner out-of-sample report card (corroborating evidence)

From `autotune_runs` (1,656 rows, 16 symphonies, 2026-05-15 → 2026-05-29):

- **`frozen_eval_sharpe`: mean −0.999, median −1.000** across 788 non-null rows — a **degenerate sentinel**, not a real OOS Sharpe. The honest hold-out fold is too thin to produce a usable risk-adjusted number, exactly as the code predicts (the ~4-usable-day validation wall).
- **`naive_sharpe` mean +1.05 → `deflated_sharpe` mean 0.00.** The BHY/deflation haircut **strips the entire in-sample Sharpe to zero**. The overfitting machinery is doing its job — and the result is that essentially **no genuine risk-adjusted edge survives deflation**.
- **`baseline_decision`: 1,292 / 1,656 (78%) "Reverted to Fallback"**, 245 (15%) "Adopted AI", 119 (7%) "Reset to Global Default". The tuner **mostly refuses to deploy** tuned parameters because they cannot clear the raised bar. This is the spec-freeze/haircut working as designed — and is itself direct evidence the data rarely supports a confident deployment.
- **`oos_alpha` contains `−inf` sentinels** (pooled mean = −inf) — a data-quality artifact to be aware of; do not aggregate this column naively.

This is independent confirmation, from the optimization side, of the same conclusion the live Guard Alpha reaches from the execution side: **the edge is not statistically distinguishable from luck at this data scale.**

---

## 5. B&H-same-symphonies baseline + regime read (the value-claim test)

The deepest north-star claim is "**early exits net-recover value vs holding**." The project's own cited authority (Kaminski-Lo 2014) makes this **regime-conditional**: stops add value under **momentum** (positive return autocorrelation), subtract value under **mean-reversion** (negative autocorrelation).

**125-day daily-close return autocorrelation, the operator's actual 11 symphonies** (reconstructed from `cache/synthetic_history_v2_*.json`; 26 of 27 cache files parsed, 1 corrupt-and-skipped):

- **Mean lag-1 autocorrelation = −0.099 (negative).**
- **Only 1 of 11 symphonies has positive autocorrelation** (iaSOOUsm, +0.092). The other 10 are negative (down to qF5ZU7AL −0.275).

**The measured regime is the UNFAVORABLE one for trailing stops** — mean-reverting / anti-momentum, where Kaminski-Lo says stops *subtract* value. This is a genuine red flag against the headline value claim, not merely a power problem.

### 5a. INTRADAY regime read (the load-bearing question, answered with the right data)

theory-auditor (via synthesizer) flagged that the alpha-half of the value claim ("net-recover more than it costs / capture upside") is **regime-conditional**: per Kaminski-Lo, stops add value under **intraday momentum** and subtract it under random-walk/mean-reversion — and whether the symphonies live in an intraday momentum regime is "established nowhere" and is the single load-bearing empirical question. §5 above used *daily-close* autocorrelation as a proxy; the engine actually stops *intraday*, so I computed the direct measure.

**Intraday minute-return lag-1 autocorrelation** (pre-trigger `shadow_history` window, minute-to-minute increments of `current_return`):
- **66 symphony-days, 16,442 minute-return pairs.**
- **Pooled lag-1 AC = −0.036, z ≈ −4.56** (distinguishable from zero, and **negative**).
- Per-series: mean −0.030, median −0.014; **only 48.5% of series have positive AC** (a coin-flip).

**Answer to the load-bearing question: the symphonies are NOT in an intraday momentum regime.** At the minute scale they are mean-reverting-to-random-walk — the regime where Kaminski-Lo says stops are neutral-to-harmful, not the momentum regime the alpha claim requires. This is the strongest single empirical statement in this audit because minute-pairs are the one abundant sample I have (n=16k).

**Honest caveat on the z-stat:** minute-to-minute returns carry microstructure autocorrelation and the pairs are themselves serially dependent, so z = −4.56 *overstates* the independent statistical power (the true effective n is much smaller than 16k). The robust, defensible takeaway is the **sign and magnitude**: intraday autocorrelation is slightly negative / essentially zero — the *opposite* of what the alpha-half of the value claim needs. The "halve drawdowns" half (mechanical left-tail truncation) needs no regime assumption and stands; the "capture upside / net-recover" half is a regime bet, and the bet is on the losing side of the cited literature here.

---

## 6. Drawdown-reduction claim ("does it ~halve drawdowns?")

Per-episode "downside avoided" = `exit_level − worst point the hold path reached after exit`:

- 20 / 22 episodes: exit dodged *some* further downside; mean downside avoided **+1.285 pp**, median +0.59 pp.
- **BUT** that transient-excursion figure (1.285pp) is ~2.7× the honest end-of-day Guard Alpha (0.47pp). The gap **is the whipsaw**: in many episodes the hold path dipped below the exit level intra-window and then **recovered above it by close**. Measuring "max adverse excursion avoided" **overstates** the benefit; the operator's actual realized P&L delta is the +0.47pp end-of-day figure.

**Verdict on the "halve drawdowns" claim:** *unsupported by the available data.* There is no clean before/after maximum-drawdown comparison computable at portfolio level from 6 days, and the intra-window "downside avoided" framing systematically flatters the result. The honest statement is "exits reduce *transient* adverse excursion in ~90% of episodes but only convert ~37% of that into realized end-of-day gain, net of whipsaw."

---

## 7. Statistical power — quantified

- Observed standardized effect (episode-level Cohen's d) ≈ **0.60**.
- n required for 80% power (α=0.05) at that effect, **assuming independence**: ~**23 observations**. Episode count (22) is right at the edge — *if* episodes were independent. **They are not.**
- Independent units available today = **5–6 trading days.** To reach 80% power on day-clustered data at the observed day-level effect (d ≈ 0.68) would need ~**20+ independent trading days** minimum, and realistically far more given regime heterogeneity — i.e. **months of live operation**, none of which exists yet.
- For the *tail-risk* machinery (CVaR-driven anything), the bar is categorically out of reach: ~1,000 tail-relevant observations needed; 125 days ≈ 6 tail days, 3 years ≈ 37 (Yamai-Yoshiba). The project already conceded and acted on this (`project_eut_cvar_migration_council_verdict`, `project_cvar_divergence_validation_wall`) — **do not re-litigate; this audit confirms it with data.**

---

## 8. Bottom line for the synthesizer

1. **Mechanism: plausible and directionally positive.** Live Guard Alpha is +0.47pp/episode, positive ~60% of the time. The shadow-history instrumentation is sound and the measurement is real (not fabricated, fully cited).
2. **Proof: absent and unreachable.** Day-clustered, the edge is not significant (t=1.52). The autotuner's own frozen-eval Sharpe is a degenerate sentinel and deflated Sharpe collapses to zero; 78% of tuning runs refuse to deploy. The 11 symphonies sit in a *mean-reverting* regime where the cited literature says stops tend to *subtract* value.
3. **The bar "proven to beat baselines" is structurally unprovable at this data scale** — confirmed independently from execution (Guard Alpha), optimization (deflated Sharpe), and theory (regime autocorrelation). This is not a defect to fix; it is a property of the problem. The honest framing the README already uses ("run paper mode and judge fit yourself", burden-on-operator) is the correct one.
4. **SPY/TQQQ baselines could not be computed** (no live Alpaca creds); B&H-same-symphonies regime read substitutes and points the same direction (unfavorable).

**Recommendation to synthesizer:** the audit should NOT certify Guard Alpha as empirically proven. It should certify the *instrumentation* as honest and the *value claim* as unproven-and-unprovable-at-scale, matching the project's own written posture. Any product copy asserting "halves drawdowns" or "proven Guard Alpha" is unsupported and should be softened to a regime-conditional, operator-evaluated claim.

---

### Appendix — artifacts & commands (provenance)

- Analysis DBs (read-only copies): `.claude/worktrees/audit-soundness/_analysis_copies/state_ro.db`, `optuna_ro.db` (copied from MAIN `alphabot_state.db`, `optuna_studies.db`; live DBs never written).
- Guard Alpha computation: per-(symphony,trading_day) over `shadow_history WHERE is_post_trigger=1`, `exit=last shadow_return`, `hold=last current_return`.
- Regime: daily-close returns = last intraday tick `return` per day from `cache/synthetic_history_v2_*.json` (26/27 files parsed; 1 corrupt skipped — `synthetic_history_v2_2026-05-14_3b5df5b2...json`, JSON truncation at char 28,225,200).
- All numbers above were produced by inline Python against the read-only copies in this session; no live DB or Alpaca write occurred. SPY/TQQQ figures were NOT produced because the data was unavailable — explicitly left uncomputed rather than fabricated.
