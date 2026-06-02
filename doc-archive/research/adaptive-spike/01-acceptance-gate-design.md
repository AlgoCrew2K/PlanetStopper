<!-- ARCHIVED from research/adaptive-spike @ 7683c30, original date 2026-05-30. Acceptance-gate design report: role-mapping (VETOES vs DISCRETIONARY), 4 new discretionary criteria (D1-D4), backtest substrate design. Implemented in Phase 3b acceptance_gate.py @ 0d79fc7. Conclusion in memory/project_adaptive_exit_direction.md. -->
# Research Report: Democratized Acceptance-Gate Design (HARD VETOES → weighted discretionary panel)

**Researcher:** acceptance-gate-designer (Agent Team `adaptive-spike`, read-only research)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2 (read-only; no app code changed)
**Confidence Summary:** The existing machinery cleanly maps to a two-stage gate — three of five components are natural HARD VETOES, two are DISCRETIONARY panel members — and the structure the user specified (vetoes-first, fixed-weight discretionary panel second, scoring only post-veto survivors) is sound *as a governance/decision architecture*. But the central honesty risk and the data wall from the prior Gate-1 work transfer in full: the panel must never resurrect a veto-failed candidate, the panel weights must be principled constants (never tuned), and on the **daily** `external_data/` panel the gate can be validated only as *decision logic over a regime-coverage substrate*, never as exit-engine replay (no intraday bars exist).

> **Scope guard (per my charter):** this report surfaces FACTS (file:line-cited), labeled INTERPRETATION, and OPTIONS + TRADE-OFFS. It does NOT recommend an implementation path, authorize a build, or pick a winner among conflicting options. The user/PM owns those decisions. Where a finding lacks a primary source it is tagged `[Unverified]` and logged as an open question.

---

## Question

This report addresses five sub-questions handed down in the dispatch brief:

1. **Role-mapping.** Of `{NN1 spec-freeze, BHY/Yekutieli haircut + N_effective, Overfitting Conscience, Spec Critic, Divergence Explainer}`, which are HARD VETOES (cannot be outvoted) vs DISCRETIONARY panel members (score only post-veto survivors)? With file:line.
2. **New discretionary criteria.** Propose 2–4 *new* discretionary criteria worth adding, each with a concrete computable definition.
3. **Integration.** Where does the two-stage gate slot into `autotuner.py` post-walk-forward acceptance, what does it return, and how is "accept new tuning vs keep incumbent" decided?
4. **Backtest substrate.** How could the acceptance gate be backtested across historical regimes on the new `external_data/` daily panel, including data-prep (synthetic-flag derivation, exclusion of synthetic pre-inception history from ground-truth splits)?
5. **H-1 dependency.** Note that the veto's significance statistic is currently mis-wired to Sortino (`autotuner.py:1251`) and must be correct for the veto to mean anything.

**Bound:** I do not re-litigate *whether* to build a democratized gate (user owns that), nor design the live exit core (that is the consensus-exit-research track's Gate-1 doc). I design the *acceptance decision* that sits on top of walk-forward, treating the live engine as the thing being tuned, not changed.

---

## Findings

### Sub-question 1 — Role mapping: VETOES vs DISCRETIONARY panel

The fatal anti-pattern the user named — *a discretionary score outvoting a failed overfitting veto = noise-laundering relocated to the gate* — dictates the partition. The test for "is this a VETO?" is: **does failing it mean the candidate is statistically or structurally illegitimate regardless of how good it looks?** If yes → hard veto, sequenced first, un-outvotable. If it is a *quality/robustness preference* among already-legitimate candidates → discretionary.

| Component | Role | Why | file:line (worktree `adaptive-spike`) |
|---|---|---|---|
| **NN1 spec-freeze** | **HARD VETO (entry-gate, pre-search)** | Refuses to even *start* a run whose spec bundle contains a `BACKTEST_SELECTION` (P&L-frozen) facet — i.e. a structurally dishonest search space. It raises `RuntimeError` and the run never produces candidates. This is the strongest possible veto: it fires *before* any candidate exists. `[High]` | `validate_search_space_nn1()` `autotuner.py:1478-1499`; `validate_nn1_compliance()` `:1501-1582`; hard-gate wiring `:1637-1640`, `:1680-1688` |
| **BHY/Yekutieli haircut + `N_effective`** | **HARD VETO (selection-gate, post-search)** | This IS the existing permission-to-deploy gate. `_haircut_select` returns `winner_trial=None` when no trial clears `p_adj <= HARVEY_LIU_FDR_Q` — the candidate set is "statistically indistinguishable from noise; reject in full." A discretionary score must never override this. `N_effective` raises the bar as the search widens. `[High]` | `_haircut_select` `autotuner.py:1184-1272` (reject branch `:1268-1271`); `compute_n_effective` `:761-811`; call-site wiring `:1957-1991` |
| **Look-ahead / purge integrity** | **HARD VETO (validity precondition)** | Not a single function but a *precondition* the brief names explicitly: the per-fold performance series feeding the significance veto must be built on the purged validation fold (`PURGE_DAYS`, `EMBARGO_DAYS`) or the whole significance computation is contaminated. A candidate whose series violates purge/embargo is illegitimate regardless of score. Today PURGE/EMBARGO are applied at fold construction (`eval_window_days` emission derives from them). `[High]` | fold-construction + emission `autotuner.py:2038-2047`; PURGE/EMBARGO usage in validation/frozen-eval `:2057-2075`; Gate-1 doc §3.1 step 4 |
| **Overfitting Conscience** | **DISCRETIONARY (today: pure observation; could be promoted to soft veto)** | Currently an *advisory-only* meta-instrument: it emits `CLEAR/WATCH/BREACH` keyed off the same `S`/`N_effective`/ratio inputs the haircut already uses, and is `is_advisory_only=1`. It does NOT block deployment today — it writes a row post-save. Because its `BREACH` is a *redundant restatement* of information the BHY veto already acts on, promoting it to a hard veto would be double-counting the same evidence; as a **discretionary** signal it is honest. `[High]` | `compute_overfitting_conscience_observation` `advisors/overfitting_conscience.py:47-176` (verdict `:124-135`, `is_advisory_only` `:174`); call site `autotuner.py:2176-2205` |
| **Spec Critic** | **HARD-VETO-ADJACENT (structural), today advisory** | Checks structural integrity of the bundle (required THEORY facets present, all disciplines recognized, no Phase-2 leak, spec age). Its `BREACH` conditions (missing facet / unrecognized discipline / phase-scope leak) are *structural illegitimacy* — the same family as NN1. But it runs `is_advisory_only=1` and does NOT block today; NN1's hard gate already rejects the worst case (`BACKTEST_SELECTION`). Spec Critic catches a *superset* of structural problems NN1 does not (e.g. missing `gamma` facet, stale spec). `[High]` | `compute_spec_critic_observation` `advisors/spec_critic.py:89-202` (BREACH resolution `:157-165`); call site `autotuner.py:1704-1707` |
| **Divergence Explainer** | **NEITHER veto NOR discretionary — pure operator diagnostic; INERT by default** | Surfaces two CVaR window values for operator eyes; **forbidden by binding constraint from carrying any signed divergence quantity** (the divergence idea was rejected — `project_cvar_divergence_validation_wall`). Default-off → writes only `NOT_APPLICABLE` rows. It carries NO accept/reject semantics and MUST NOT be wired into the gate as either a veto or a scored criterion — doing so would re-import the rejected divergence-detector. `[High]` | `compute_divergence_explainer_observation` `advisors/divergence_explainer.py:65-141` (forbidden-keys doctrine `:7-17`, NOT_APPLICABLE default `:99-109`); call site `autotuner.py:2206-2212` |

**Summary partition (the answer):**

- **HARD VETOES (sequenced first, un-outvotable):** (1) NN1 spec-freeze, (2) BHY/Yekutieli significance + `N_effective`, (3) look-ahead/purge integrity. These are exactly the three the brief named. **All structural/statistical-legitimacy gates.**
- **DISCRETIONARY panel candidates (score post-veto survivors only):** Overfitting Conscience (as a soft quality signal, NOT a re-veto). Spec Critic is **structurally a veto by nature** but is *implemented* as advisory — the user must decide whether to promote it to a hard structural veto (see Options) or leave it discretionary.
- **EXCLUDE entirely from the gate:** Divergence Explainer (operator diagnostic; wiring it in resurrects a rejected idea).

> **INTERPRETATION (labeled):** My reading is that **today there is effectively ONE hard veto doing real work (the BHY haircut), one structural hard gate that fires pre-search (NN1), and a purge-integrity precondition** — and the three advisor producers are governance/observation instruments, not deciders. The "democratization" the user wants is therefore *net-new*: there is no existing weighted discretionary panel; the panel would be built from scratch, scoring candidates that survive the BHY veto. This matters because it means the discretionary layer is the *new codepath* (Agent-Teams TDD), while the vetoes mostly already exist (and one is mis-wired — H-1).

---

### Sub-question 2 — New discretionary criteria (computable definitions)

These score **only candidates that already passed all three hard vetoes**. They are quality/robustness *preferences*, never legitimacy gates. Each is defined to be computable from artifacts the autotuner already produces or could produce per-fold. **Critical constraint the user set: the panel weights are FIXED principled constants, NEVER learned/tuned** — so each criterion below ships with a *suggested fixed weight rationale*, not a tunable weight.

> **Hard honesty caveat carried from Gate-1 §4 (do not bury):** at this data scale the discretionary panel cannot *manufacture* validation budget. Adding criteria that each consume the same ~4-usable-day fold does not add independent evidence — it adds *correlated views of the same thin sample*. The panel's honest function is **tie-breaking and robustness-preference among candidates the veto already blessed**, NOT generating new confidence. A panel that "feels richer" by adding criteria while the underlying sample stays at ~4 days is decoration, not power. I state each criterion's computability AND its data-wall exposure.

**Criterion D1 — Regime-robustness across folds (cross-fold score dispersion).**
- **Definition:** Partition the available history into K non-overlapping purged sub-folds. For each candidate parameter set, compute the per-fold objective (the SAME objective the veto uses — CRRA-EU per-day series), giving a vector `[obj_1 … obj_K]`. Score = a dispersion-penalized central tendency, e.g. `median(obj_k) − λ·IQR(obj_k)` with **λ a fixed principled constant** (not tuned). Higher = more consistent across regimes; a candidate that wins one fold and craters in another scores low.
- **Why discretionary not veto:** robustness is a *preference* among legitimate candidates, not a legitimacy test.
- **Data-wall exposure:** `[High]` HEAVY. K sub-folds of a ~125-day record at PURGE=20/EMBARGO=1 leaves very few usable days per fold; the dispersion estimate is itself noisy. This is the criterion the prior CPCV discussion (Gate-1 §3.3) targets — combinatorial purged CV is the named-but-unimplemented machinery (`autotuner.py:368-370`) that would make this estimate less variance-dominated. `[interpretation]` Without CPCV this criterion is directionally honest but statistically weak.
- **Computable from:** `_collect_sim_returns` over multiple purged folds (`autotuner.py:2060`, `:2071`); requires a fold-partition loop that does not exist today.

**Criterion D2 — Parameter stability vs incumbent (move-magnitude penalty).**
- **Definition:** `stability = −Σ_i |p_candidate,i − p_incumbent,i| / scale_i` over the tuned parameter vector, where `scale_i` is each parameter's search-range width (so the penalty is unitless). A candidate that proposes a large jump off the last-known-good params scores worse, all else equal. This is the *governance* expression of "the burden of proof is on deviation" (Gate-1 §2.2).
- **Why discretionary:** a large move is not illegitimate — it just demands more evidence; the BHY veto already supplies the legitimacy bar. This biases tie-breaking toward incumbent persistence.
- **Data-wall exposure:** `[High]` LOW — purely a function of the candidate and incumbent parameter vectors, no new statistical estimate. This is the *safest* new criterion: it consumes no sample budget. It directly encodes the forecast-combination-puzzle lesson (estimated moves underperform OOS on small samples; Gate-1 §1, §4).
- **Computable from:** `best_params` vs `current_params`/`fallback_params` already in scope at the cascade (`autotuner.py:2019-2021`, `:2050`); `OPTUNA_SEARCH_SPACE_KEYS` gives the parameter set and ranges.

**Criterion D3 — Drawdown-profile preference (downside-shape, not magnitude).**
- **Definition:** From the candidate's per-fold realized return series, compute a *shape* statistic that rewards left-tail truncation without rewarding pure return: e.g. `−CVaR_5%(returns)` (less-negative tail = better) OR the ratio of downside semideviation to total deviation. Score the *protective profile* the engine exists to deliver (Gate-1: the "halve drawdowns" leg is the theoretically grounded half).
- **Why discretionary:** among legitimate candidates, prefer the one whose realized loss profile is gentler — a preference, not a legitimacy test. Aligns the panel with the engine's *grounded* value claim rather than its *contraindicated* upside-capture claim (audit §3, §6).
- **Data-wall exposure:** `[High]` HIGH — CVaR at 5% over a ~4-day fold is essentially unestimable (`project_cvar_divergence_validation_wall`; Yamai-Yoshiba ~1,000 tail obs vs ~6-37 available, audit §5). This criterion is honest only as a *coarse directional* preference (e.g. binary "did the worst fold-day exceed a fixed floor"), never as a precise CVaR number. **Flag: if implemented as a precise CVaR score, it re-imports the exact wall the project walled off twice.** Recommend the coarse-shape form or omit.
- **Computable from:** per-fold return series via `_collect_sim_returns`; CVaR machinery exists (`math_engine.compute_portfolio_cvar`) but its small-sample invalidity is documented.

**Criterion D4 — Prior-anchoring / theory-consistency (distance from theory-frozen prior).**
- **Definition:** `−Σ_i |p_candidate,i − p_prior,i| / scale_i` where `p_prior` is the *theory-frozen* warm-start (equal-ish weights, trailing-stop-up, VWAP-Breakdown-down per Gate-1 §2.2), distinct from D2's *incumbent* anchor. Rewards candidates that stay close to the theoretically-justified anchor.
- **Why discretionary:** theory-consistency is a soft preference; the NN1 veto already enforces *structural* theory-freeze, but NN1 does not score *how far a candidate's numeric values drift from the theory prior* — D4 does.
- **Data-wall exposure:** `[High]` LOW — function of candidate vs a fixed prior vector; consumes no sample. Pairs with D2 (incumbent-anchor) to express "prefer candidates near BOTH the last-known-good AND the theory anchor."
- **Computable from:** candidate params vs a stored theory-prior facet (the prior-storage seam the Gate-1 doc §2.2 describes; `[Unverified]` whether such a prior facet exists today — would need to be added as a frozen `spec_facet`).

> **My interpretation of the panel as a whole:** D2 and D4 (both pure parameter-distance penalties, zero sample cost) are the *honest backbone* of a discretionary panel at this data scale — they encode "prefer small, theory-consistent moves" which is exactly what the forecast-combination literature says is right on thin samples. D1 and D3 are *directionally correct but sample-starved*; they are honest only in coarse/robust forms and become dishonest the moment they are read as precise statistics. A panel built mostly of D2+D4 with D1/D3 as coarse robustness nudges is the honest shape; a panel that leans on precise D1/D3 estimates is decoration over a thin sample. **This is interpretation, not a directive — the user chooses the panel composition.**

---

### Sub-question 3 — Integration with autotuner post-walk-forward acceptance

**Where it slots.** The acceptance decision today is a strict three-stage cascade inside the per-symphony loop of `run_autotuner`:

1. **BHY veto** (`_haircut_select`, `autotuner.py:1947-1991`) → sets `winner_trial` or `haircut_rejected_proposal=True`.
2. **Schema validation** (`:2001-2016`) → poisons invalid proposals to `oos_alpha=-inf`.
3. **Three-way OOS comparison** (`:2090-2107`) → `"Adopted AI"` iff `oos_alpha > fallback_oos_alpha AND oos_alpha > default_oos_alpha` (strict, plus `>0` reporting); else `"Reverted to Fallback"` or `"Reset to Global Default"`. The chosen params are written into `current_params`.

The two-stage democratized gate maps onto this cleanly:

- **Stage 1 (HARD VETOES) = the existing flow up through the BHY veto + schema + purge precondition.** No change to the *order* — vetoes already run first. The only required *correctness* fix is H-1 (the veto currently scores the wrong statistic; see sub-question 5). NN1 already fires earlier as a hard pre-search gate.
- **Stage 2 (DISCRETIONARY weighted panel) = a NEW scoring step inserted between the veto-survivor set and the final `baseline_decision`.** Concretely: only candidates that (a) cleared the BHY gate (`winner_trial is not None`) and (b) beat both baselines in OOS would be *eligible*; the panel then produces a fixed-weight composite score used to decide **accept-new vs keep-incumbent** when the OOS comparison is close or when multiple veto-survivors exist.

**What it returns (proposed shape, NOT a directive):** a structured `AcceptanceVerdict` with:
- `vetoes_passed: bool` + per-veto detail (which veto, the statistic, the threshold) — drawn from existing `winner_p_adj`, `winner_tstat`, NN1 result.
- `panel_score: float | None` (None when no candidate survived the vetoes — the panel never runs on a veto-failure, structurally enforcing the anti-pattern prohibition).
- `panel_breakdown: dict` (per-criterion sub-scores × fixed weights) for operator transparency.
- `decision: "ADOPT_CANDIDATE" | "KEEP_INCUMBENT" | "RESET_DEFAULT"`.

**How "accept new vs keep incumbent" is decided (the load-bearing rule):** The decision MUST be lexicographic, vetoes-dominant:

```
if not all_vetoes_passed:            decision = KEEP_INCUMBENT (or RESET_DEFAULT per existing cascade)
elif candidate not OOS-superior:     decision = KEEP_INCUMBENT          # existing :2090 rule
elif panel_score(candidate) >= panel_score(incumbent) + MARGIN:  decision = ADOPT_CANDIDATE
else:                                decision = KEEP_INCUMBENT          # panel says the move isn't worth it
```

`MARGIN` is a **fixed principled constant** (the panel's "burden of proof on deviation" expressed as a score hurdle), NOT tuned. **The panel can only ever make the gate STRICTER** (block an OOS-superior, veto-passing candidate because its robustness/stability profile is poor) — it can NEVER resurrect a candidate the vetoes killed. This is the structural enforcement of the user's forbidden anti-pattern: `panel_score` is `None` whenever vetoes fail, so there is no code path where a panel score is even computed for a veto-failed candidate, let alone used to overturn the veto.

> **INTERPRETATION:** the cleanest honest framing is **"the panel is a one-directional brake, never an accelerator."** It exists to *withhold* adoption from a candidate the thin-sample veto technically blessed but whose robustness profile is suspect — biasing the system toward incumbent persistence (which the data wall says is the correct default ~78% of the time anyway, Gate-1 §1). If the user instead wants the panel to be able to *promote* a candidate that narrowly missed OOS-superiority, that is a different (and more dangerous) design — it widens the gate, and at this data scale widening the gate is the documented road to overfitting. I flag both; the user chooses.

**Blast-radius note (FACT):** the OOS cascade at `:2090-2107` writes directly into `current_params`, which becomes the live engine's parameters. So the acceptance gate is **on the path that determines live trading parameters** — but it runs in `autotuner.py`, post-walk-forward, NOT on the 1-minute execution path (architecture constraint #1 is respected; the advisor producers are deliberately placed here for that reason, `autotuner.py:2206-2208` comment). A new panel step here does not violate the no-blocking-I/O-on-execution-path rule. `[High]`

---

### Sub-question 4 — Backtesting the gate on the `external_data/` daily panel

**The hard ceiling (state this first, unhedged).** `external_data/consensus_prices.csv.gz` is a **DAILY** panel: `[ticker, date, adj_close, daily_return, source1, source2, confidence]` — **no OHLC, no volume, no intraday bars, no VWAP inputs.** Planet Stopper's entire exit engine is INTRADAY (trailing-stop ticks, VWAP breakdown/bleed, log-time intraday squeeze). **Therefore the daily panel CANNOT replay the live exit engine at all.** It can validate exactly one thing: **the acceptance-gate DECISION LOGIC as a meta-decision** — does the gate correctly accept/reject candidate tunings given a per-fold performance series, and does its veto-then-panel ordering behave correctly across historically distinct regimes? `[High]` (adaptive-frontier-researcher confirmed this framing identically, 2026-05-30: the daily panel is their notch-3 DATA-BLOCKED binding gap — gate-logic substrate + regime-coverage counting, NOT exit-engine replay. Both reports state this identically.)

**What a gate-logic backtest on daily data CAN do:**
1. **Regime-coverage stress of the veto.** Slice the daily panel into historically labeled regimes (1929, 1973-74, 1987, 2000-02, 2008, 2020, 2022 — each a known drawdown regime present in the 1885→2026 span). For each regime, construct per-fold daily return series for a basket of candidate "tunings" (here a tuning is a *daily* exit rule proxy, e.g. a daily trailing-stop fraction — explicitly a PROXY, not the intraday engine). Then verify: **does the BHY veto correctly refuse to deploy in regimes where the candidate's edge is noise, and does it permit only where a genuine cross-regime signal exists?** This tests the *gate*, not the engine.
2. **Anti-pattern regression.** Construct adversarial candidates: one that FAILS the overfitting veto but scores *high* on the discretionary panel (e.g. a curve-fit-to-one-regime tuning with low parameter-move distance). Assert the gate returns `KEEP_INCUMBENT` — the panel score must be `None`/ignored because the veto failed. This is the single most important backtest: it operationalizes the user's forbidden anti-pattern as a falsifiable test across real historical regimes.
3. **Independent-regime counting.** Use the panel's 140-year span to *count* how many genuinely independent drawdown regimes the data contains (the binding constraint on the veto, per Gate-1 §1 "regimes not days"). This calibrates how many discretionary criteria the panel can honestly carry.

**Data-prep (the four steps the brief named):**

1. **Derive the synthetic/leverage flag from the source columns.** `ticker_metadata.csv.gz` has NO leverage/asset-class/synthetic flag (cols are `[ticker, fractionable, tradable]` booleans only). The ONLY synthetic signal is in `consensus_prices` `source1`/`source2 ∈ {alpaca, eodhd, fmp, tiingo, synthetic}`. **Derive `is_synthetic_row = (source1 == 'synthetic') OR (source2 == 'synthetic')`** per (ticker, date) row. `[High]` — this is the only derivation the data supports; there is no metadata shortcut.
2. **Derive a per-ticker inception date.** `inception_date(ticker) = min(date WHERE NOT is_synthetic_row)` — the first date the ticker has a *real* (non-modeled) price. Everything before that is synthetic back-fill (UPRO modeled to 1885, TQQQ to 1995 are the brief's named examples).
3. **EXCLUDE synthetic pre-inception history from any ground-truth validation split.** Any fold used as the *held-out truth* the gate's decision is scored against MUST contain only `is_synthetic_row == False` rows after `inception_date`. Synthetic rows may be used for *context/feature* history but NEVER as the ground truth a deploy/reject decision is validated against — otherwise the gate is validated against a model's own extrapolation (circular; the validatability equivalent of the parser+fixture co-design fail). `[High]`
4. **Respect survivorship + the daily-vs-regime gap.** `ticker_metadata` has `tradable` but the panel is survivor-biased toward tickers that exist in 2026; and (critically) the gate's veto reads *regimes*, not autocorrelated days — so even 35,359 trading days do NOT buy 35,359 independent observations. The independent-regime count (asked of adaptive-frontier-researcher) is the real ceiling, and survivorship + synthetic-fill push it *below* the naive day count.

> **INTERPRETATION:** the daily panel is genuinely valuable for ONE purpose the live + ~125-day record cannot serve — **stress-testing the gate's DECISION LOGIC across many more historically distinct drawdown regimes than the live era contains.** That is a real, honest use: it can show the veto-first ordering holds up in 1929 and 2008, and it can run the anti-pattern regression against real crises. What it categorically cannot do is validate the *exit engine* (no intraday data) or *manufacture intraday-regime validation budget* for the live strategy. The danger is mistaking "the gate logic behaved correctly across 140 years of daily regimes" for "the tuned strategy is validated" — they are different claims, and only the first is supported. `[interpretation, grounded in the data schema + Gate-1 §4]`

---

### Sub-question 5 — H-1 dependency (the veto's significance statistic is mis-wired)

**FACT, confirmed by direct read of the worktree (`adaptive-spike` HEAD 8586ab2):** `_haircut_select` accepts a `tstat_fn` parameter (`autotuner.py:1184`) and its docstring states swapping it "is the ONLY permitted change" (`:1203-1207`); the call site correctly routes `_tstat_fn = compute_crra_eu_tstat` for CRRA-EU bundles (`:1953-1954`) into `_haircut_select(..., tstat_fn=_tstat_fn, ...)` (`:1976-1977`). **But the loop body hardcodes `tstats.append(compute_sortino_tstat(series, seed=trial_idx))` (`:1251`) and never calls `tstat_fn`** — it computes `_crra_gamma` (`:1243`) and discards it. This exactly matches audit H-1 and Gate-1 §3.1.

**Why this is load-bearing for the democratized gate:** the BHY haircut IS hard veto #2. If its significance statistic is the wrong one (Sortino, a ratio) for the canonical CRRA-EU objective (a mean-valued functional), then **every accept/reject decision the veto makes is calibrated against a sampling distribution unrelated to the objective being optimized.** A democratized gate built on top of this would have its *primary legitimacy veto* deciding on noise — and the discretionary panel would then be scoring candidates the veto admitted for the wrong reason. **The veto must be correct before "vetoes cannot be outvoted" means anything**; an incorrect veto that admits the wrong candidates is functionally as bad as letting the panel overturn it.

**This is a pre-existing LIVE defect** (audit confirms runtime reachability on the canonical THEORY-bundle path) and is a hard prerequisite, not a parallel nicety. It is also fixable independently of this experiment. `[High]` — triangulated across this direct read, the audit synthesis H-1, and the Gate-1 doc §3.1 (three independent corroborations; note these partly share the audit upstream, so I count it as two independent reads: my own + the audit, with Gate-1 derivative of the audit).

---

## Sampler/Pruner Recommendations

**Not applicable in the conventional sense, and that is itself a finding.** The democratized acceptance gate is a *post-hoc decision layer over completed Optuna trials*, not a sampler/pruner choice. The existing study uses Optuna to generate the candidate trials whose `daily_returns` user-attr the haircut consumes (`autotuner.py:1247`). The gate does not change sampling.

| Concern | Observation (trade-off, not directive) |
|---|---|
| Does adding a discretionary panel change sampler choice? | No. The panel scores *completed* trials post-selection. Sampler/pruner choice is orthogonal. `[High]` |
| Does the panel interact with pruning? | If criterion D1 (cross-fold robustness) needs per-fold series for *many* candidates, aggressive pruning (e.g. median pruner killing trials early) would starve D1 of the multi-fold series it needs. A panel leaning on D1 implies retaining more complete trials → higher compute. `[interpretation]` |
| `n_trials` budget vs panel richness | Per my charter's anti-pattern: do NOT inflate `n_trials` to "feed" a richer panel. The data wall is regimes, not trials; more trials widen the search → raise `N_effective` → stricter veto, and do not buy the panel more independent evidence. The current 500-trial/100-floor regime (project CLAUDE.md gotcha) is unaffected by the panel. `[High]` |

---

## Reproducibility Checklist

| Item | Status / caveat | Citation |
|---|---|---|
| Deterministic veto re-runs | The haircut already seeds per-trial deterministically (`compute_sortino_tstat(series, seed=trial_idx)`, `autotuner.py:1248-1251`) so an identical study yields an identical veto decision — but this is the *mis-wired* call; the H-1 fix must preserve the same determinism pin (seed = stable within-study trial index) for `tstat_fn`. | `autotuner.py:1246-1251` |
| Panel determinism | A fixed-weight panel over deterministic per-fold series is itself deterministic — NO new RNG, provided the fold partition (D1) is fixed/seeded and `_collect_sim_returns` is deterministic for given params. Any stochastic CVaR/bootstrap inside D3 must carry an explicit seed. `[interpretation]` | `_collect_sim_returns` `autotuner.py:2060` |
| Study-name discipline | Unchanged — `<timestamp>__<symphony>`, never reused (project gotcha). The panel adds no studies. | project CLAUDE.md |
| `N_effective` charging | If the panel introduces *any* new tunable into the Optuna search space, it MUST be charged into `compute_n_effective` (Gate-1 §2.3 hinge). A fixed-weight panel introduces NO new search-space tunables → `N_effective` unchanged → veto bar unchanged. This is the panel's safety property: fixed weights = zero added DoF. | `compute_n_effective` `autotuner.py:761-811`; Gate-1 §2.3 |
| Optuna RNG-under-parallelism caveat | `[Unverified]` — I did not verify whether the autotuner runs trials in parallel; if it does, the per-trial `seed=trial_idx` determinism pin assumes a stable trial ordering that parallel execution can perturb. Logged as open question. | — |

---

## Statistical Validity

**The gate's vetoes inherit the project's existing (sound-but-underpowered) statistical posture; the discretionary panel adds no new validity and must not pretend to.**

- **Purge/embargo (the look-ahead veto):** the per-fold series feeding the significance veto must be built on the purged validation fold (`PURGE_DAYS`, `EMBARGO_DAYS`). Today these are applied at fold construction (`autotuner.py:2038-2047`). Any new criterion (D1's sub-folds especially) MUST re-apply purge+embargo at *every* sub-fold boundary or it silently reintroduces look-ahead — this is the look-ahead veto applied recursively. `[High]`
- **Regimes, not days (the binding constraint):** the BHY veto's √T legitimately uses the day count, but significance must be *read* against independent regimes. The Gate-1 doc puts the live + ~125-day record at ~5-15 independent regime units. The `external_data/` daily panel raises the *regime* count for gate-LOGIC backtesting (more historical crises) but NOT the live strategy's intraday-regime budget (no intraday data). A naive per-day t-stat over autocorrelated days overstates effective T — a clustered/block SE is needed; this is flagged as the gate-1 doc's `[interpretation]` needing a statistician. `[Medium]`
- **CVaR criteria (D3) hit the rejected wall:** `project_cvar_divergence_validation_wall` and audit §5 establish that 5% CVaR over a ~4-day fold is unestimable. D3 in its *precise* form re-imports this wall; only its coarse-directional form is honest. `[High]`
- **The panel cannot escape the wall by reframing** — the project proved twice (EUT+CVaR migration, CVaR-divergence detector) that reframing relocates the data wall, it does not buy validation budget. A discretionary panel over the same thin sample is the same wall applied to a richer-looking decision surface. `[High]`

**Net:** the gate is statistically valid *as a decision architecture* — vetoes-first is correct, fixed-weight panel-second adds zero DoF and cannot overfit by construction — but it is NOT a path to validating the tuned strategy. Its honest output, like the existing engine, is "mostly keep the incumbent."

---

## Interpretation

*(Labeled explicitly — may be wrong.)*

My interpretation of the findings, if the project's existing assumptions hold:

1. **The democratized gate is mostly a re-organization + correctness fix of what exists, plus one genuinely new layer.** The three hard vetoes already exist (NN1, BHY, purge); the only *new* code is the discretionary panel and its lexicographic integration. The most urgent work is not the panel — it is fixing H-1 so the primary veto decides on the right statistic.
2. **The honest panel is a one-directional brake built mostly from zero-sample-cost criteria (D2 parameter-stability, D4 prior-anchoring).** Criteria that consume the thin sample (D1 robustness, D3 drawdown) are honest only in coarse forms and become decoration-over-noise in precise forms. The panel's value is *governance transparency* (operator can see WHY a candidate was withheld) and *incumbent-bias* (correct at this data scale), not added statistical power.
3. **The daily `external_data/` panel is a gate-LOGIC stress substrate, not a strategy-validation substrate.** Its highest-value use is the anti-pattern regression across real historical crises — proving the veto-first ordering survives 1929/2008/2020. Mistaking that for strategy validation would repeat the exact over-claim the audit warns against.
4. **The whole design is "safe-by-degeneracy" in the same way the consensus-exit core is** (Gate-1 §4): fixed weights + un-outvotable vetoes + incumbent-default means the expected behavior is "keep the incumbent," and that degeneracy is precisely what keeps it honest. Anyone who needs the gate to *visibly* adopt new tunings often will be tempted to loosen the veto or let the panel promote — both of which break the honesty.

---

## Options & Trade-offs

*(NOT recommendations. The user/PM decides.)*

**O1 — Spec Critic: promote to hard structural veto vs leave advisory.**
- *Promote:* catches structural illegitimacy NN1 misses (missing `gamma` facet, unrecognized discipline, Phase-2 leak) as a true blocker. Cost: a `BREACH` would now refuse to deploy, changing behavior; needs its own RED tests; risk of false-blocking on a benign stale-spec WATCH.
- *Leave advisory:* zero behavior change; structural problems surface to `/ai-advisor` but don't block. Cost: a structurally broken bundle that NN1 doesn't catch could still deploy.

**O2 — Discretionary panel composition.**
- *Lean (D2 + D4 only):* zero sample cost, pure parameter-distance brakes, cannot overfit, maximally honest. Cost: less "rich," no explicit robustness signal.
- *Full (D1–D4):* explicit cross-fold robustness + drawdown shape. Cost: D1/D3 consume the thin sample, honest only in coarse forms, and D3-precise re-imports the CVaR wall. Higher compute (retain more complete trials).

**O3 — Panel directionality.**
- *One-directional brake (panel can only WITHHOLD adoption):* structurally cannot overturn a veto or promote a non-OOS-superior candidate; biases to incumbent. Honest-by-construction.
- *Bi-directional (panel can also PROMOTE a near-miss candidate):* more "adaptive-feeling." Cost: widens the gate; at this data scale, widening the gate is the documented overfitting road. Higher honesty risk.

**O4 — Backtest ambition on `external_data/`.**
- *Gate-logic-only (anti-pattern regression + regime-coverage counting):* honest, supported by the daily data, high value.
- *Attempt strategy proxy validation (daily exit-rule proxy as a stand-in for the intraday engine):* tempting but the proxy is NOT the engine; any "validation" is of the proxy, not Planet Stopper. Risk of over-claim.

**O5 — Overfitting Conscience role.**
- *Keep as discretionary observation:* avoids double-counting the evidence the BHY veto already acts on. Honest.
- *Promote to a second hard veto:* double-counts the same `S`/`N_effective` evidence; would make the gate stricter for a reason already covered. Likely redundant.

---

## Open Questions

1. **Independent-regime count from the daily panel — RESOLVED by adaptive-frontier-researcher (2026-05-30).** The firmed answer has TWO uses of ONE budget: (a) for the gate-LOGIC backtest, the historical daily panel DOES materially raise the count of distinct historical crisis/regime episodes the gate's accept/reject logic can be exercised against (decades of real non-synthetic history contain many more independent regimes than the ~6 live days) — so gate-logic validatability genuinely improves on the new data; (b) for the LIVE STRATEGY's exit-response budget, it does NOT rise — it stays ~5-15 independent regime units (Gate-1 §4.2 / pillar3 t=1.52), because the response must validate against intraday exit outcomes the daily panel cannot supply, and survivorship + 7%-synthetic-fill + cross-ticker correlation cap the effective independent count far below the raw 35,359-day count. **Net: classifier/gate-logic side = data-rich and improvable; exit-response side = stuck at ~5-15 → ≤1 honestly-learnable response parameter** — which is exactly the panel's "vetoes-then-brake → mostly keep the incumbent / safe-by-degeneracy" output. `[High — corroborated by adaptive-frontier-researcher's report §3-§4 + Gate-1 §4.2 + pillar3]` This confirms the panel can honestly carry only a *small* number of discretionary criteria on the live-response side (the D2+D4 zero-sample-cost backbone), while the gate-logic backtest across historical regimes is the genuinely data-rich use.
2. **Does a theory-frozen *prior* facet exist today** for D4's prior-anchor, or must it be added as a new frozen `spec_facet`? The Gate-1 doc §2.2 describes a prior-storage seam but I did not confirm a prior facet is currently populated. `[Unverified]`
3. **Optuna parallelism** — is the autotuner running trials in parallel? Affects whether the `seed=trial_idx` determinism pin is stable under the H-1 fix. `[Unverified]`
4. **CPCV appetite** — D1 (cross-fold robustness) is variance-dominated without the named-but-unimplemented combinatorial purged CV (`autotuner.py:368-370`). Whether the team will take CPCV on is a Gate-1-level decision (carried from Gate-1 doc §3.3, OQ-6). `[interpretation]`
5. **MARGIN constant calibration** — the panel's "burden of proof on deviation" score hurdle must be a principled fixed constant; what principled basis sets its value? Open (analogous to the FDR `q` choice). `[Unverified]`
6. **Adjacent (logged, not pursued):** whether the live exit engine should itself become the weighted-consensus core (the consensus-exit-research track's question) is OUT of this report's scope — this report tunes/accepts the engine as-is, it does not redesign it.

---

## Sources

| # | Source | Access date | Tier | Description |
|---|---|---|---|---|
| 1 | `autotuner.py:761-811`, `:1184-1272`, `:1247-1251`, `:1478-1582`, `:1637-1707`, `:1947-2107`, `:2176-2212` (worktree `adaptive-spike`, HEAD 8586ab2) | 2026-05-30 | 1 (primary, project source) | The veto machinery (`_haircut_select`, `compute_n_effective`), NN1 gates, OOS cascade, advisor call sites. H-1 confirmed at `:1251`. |
| 2 | `advisors/overfitting_conscience.py:47-205`, `advisors/spec_critic.py:89-236`, `advisors/divergence_explainer.py:65-199` (same worktree) | 2026-05-30 | 1 (primary, project source) | The three advisor producers — verdict logic, `is_advisory_only` flags, forbidden-keys doctrine. |
| 3 | `math_engine.py:826-859` (same worktree) | 2026-05-30 | 1 (primary, project source) | `resolve_trigger_priority` — the 4 voter flags and canonical priority order. |
| 4 | `consensus-exit-research/research/00-GATE1-RECOMMENDATION.md` (synthesizer, 2026-05-30) | 2026-05-30 | 1 (project internal knowledge base) | Weighted-consensus Gate-1 decision doc: data wall, H-1, H-3, dimensionality, validatability §1-§7. |
| 5 | `audit-soundness/audit/00-SYNTHESIS.md` (synthesizer, 2026-05-30) | 2026-05-30 | 1 (project internal knowledge base) | Soundness audit synthesis: H-1 runtime confirmation, regime/data-wall findings, CVaR-tail unestimability §5. |
| 6 | Project memory: `project_cvar_divergence_validation_wall`, `project_eut_cvar_migration_council_verdict` | 2026-05-30 | 1 (project decision log) | The twice-rejected reframing walls — D3 and Divergence-Explainer exclusion rationale. |
| 7 | `external_data/` recon facts (supplied in dispatch brief; verified by another agent, not by me — I cannot decompress the .gz) | 2026-05-30 | 2 (relayed verified) | Daily panel schema, synthetic-source derivation, survivorship — used for sub-question 4. Labeled relayed; not independently decompressed. |
