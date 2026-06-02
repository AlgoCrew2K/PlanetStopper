<!-- ARCHIVED from research/adaptive-spike @ 7683c30, original date 2026-05-30. Conclusion adopted in Phase 3 build (memory/project_adaptive_exit_direction.md): democracy on acceptance gate, not live exit; ~1-knob adaptive budget; reactive layer first. Phase 3 complete @ c23153c. -->
# The Adaptive System You CAN Honestly Build — Synthesis Recommendation

**Synthesizer:** synthesizer (Agent Team `adaptive-spike`, NON-TDD read-only research; synthesizing lead)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2 (read-only research; no app code changed)
**Inputs (read in full, adversarially cross-verified against source):**
- `research/01-acceptance-gate-design.md` — acceptance-gate-designer (HARD VETOES → discretionary panel; integration; daily backtest substrate; H-1)
- `research/02-adaptive-frontier.md` — adaptive-frontier-researcher (the honest maximum of adaptivity: reactive / learned / data-blocked tiers; intraday-depth answer)
- Prior Gate-1 doc: `consensus-exit-research/research/00-GATE1-RECOMMENDATION.md` (data wall, H-1/H-3, ~5-15-regime count, safe-by-degeneracy)

**Direct-source verification performed (charter requirement — down-rank uncited claims):**
- **H-1 VERIFIED by my own read** at `autotuner.py:1251`: the loop hardcodes `compute_sortino_tstat(series, seed=trial_idx)` and never calls the `tstat_fn` parameter; the inline comment at `:1240-1243` documents the category error and discards `_crra_gamma`. Both researchers' H-1 claim is exact.
- **Reactive layers VERIFIED by my own read** at `math_engine.py:294-325` (`compute_time_squeeze_decay`: `decay_curve = 1.0 - math.sqrt(1.0 - time_ratio)`, comment states "Zero free parameters — THEORY provenance," Danielsson & Zigrand 2003) and `:328-372` (`compute_active_trailing_stop`: `active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)`). adaptive-frontier's "engine is already reactive at ~0 free params" claim is exact.

> **For the user (who is NOT giving up on adaptive).** This document does not tell you to abandon adaptive. It tells you, with the file:line receipts checked, **exactly which adaptive system you CAN honestly build right now, what it will learn, what it cannot learn, what the new daily data buys you, and what — definitively — remains blocked and by what data.** Two independent research tracks, from two different directions (the acceptance-gate governance side and the regime-frontier side), landed on the **same single number** for how much this data can honestly drive: **about one knob.** That convergence is the spine of this recommendation.

---

## 0. The honest frame: REACTIVE is buildable now; ADAPTIVE-LEARNED is mostly data-blocked — but the slice that survives is real

"Adaptive" splits cleanly into two things that have very different honesty profiles, and conflating them is where the danger lives:

- **REACTIVE** = the engine's knobs respond to *currently observable* conditions via **theory-fixed** curves that *estimate nothing*. This is **achievable now**, adds **~0 validation burden**, and the engine **already does a lot of it** (verified: inverse-vol stop scaling + zero-free-parameter √-time squeeze, `math_engine.py:294-372`).
- **ADAPTIVE-LEARNED** = the engine's knobs are *fitted from history* and shift with a *learned* regime. This is where the data wall bites: you **can** honestly learn the regime *label* (abundant daily observations), but you **cannot** honestly learn more than **≈1 response parameter** hung off it (the response validates against ~5-15 independent regime units, not against daily rows).

**The single honest sentence (synthesized from both tracks):** *You can extend the engine's already-reactive knobs to condition on a regime label today, you can learn a coarse 2-3 state daily regime classifier from the real broad history, and you can let that classifier move at most one theory-anchored response knob under the existing permission discipline — and you can wrap all of it in a democratized acceptance gate whose vetoes can never be outvoted. Everything richer than that is the data wall the project already mapped twice.*

**The headline tension the user must hold (true on BOTH tracks):** the most defensible *direction* the theory licenses — let stops fire where the underlying trends, stand down where it mean-reverts (Kaminski-Lo 2014) — points the honest response toward **restraint**, because the operator's symphonies *measure* as the mean-reverting intraday regime (pillar3: lag-1 AC ≈ −0.036, z ≈ −4.56). So an honest adaptive layer will most often counsel *less* firing, not more. That is the same "your expectation must be confronted up front" issue Gate-1 raised — surfaced here as a frontier fact, not a gate caveat.

---

## 1. The adaptive system you CAN honestly build (concrete, two layers)

This is a **single coherent system in two cooperating layers**: a regime-response layer (the "adaptive" the user wants) and an acceptance-gate democracy (the governance that keeps it honest). They share the same ≈1-knob budget and the same permission machinery.

### 1A. The reactive / regime-response layer — achievable NOW

**What already exists (verified, the floor you build on):**

| Reactive layer | Mechanism | Free params | Code (verified) |
|---|---|---|---|
| Inverse-vol stop scaling | `active = max(safe_vol × dynamic_multiplier, dynamic_min_stop)` — wider stops when vol is high | multiplier is tuned; *curve* is linear | `math_engine.py:328-372` |
| √-time intraday squeeze | `decay = 1 − √(1 − time_ratio)`; interpolates MULT_OPEN→MULT_CLOSE over the session | **zero** (closed-form THEORY) | `math_engine.py:294-325` |
| MC recovery gate | suppress exit when MC "could recover" probability clears a bar | gate params | `math_engine.py` MC layers |
| Breakeven latch | one-way lock to breakeven after HWM-hold ticks | clamps | `math_engine.py:375+` |

**The honest extension (the new adaptive notch you can build now):** make those *existing* knobs **condition on a regime label**. Two honest ways to source the label:

1. **Theory-fixed label (cheapest, ~0 added DoF).** A non-estimated classifier — realized-vol terciles or a trend-sign threshold on a market proxy. Because nothing is fitted, it inherits the √-time layer's "honest because nothing is estimated" property and adds **~0 validation burden**. Direction from Kaminski-Lo 2014 (`[Theoretical+Backtest, High]`) and Moreira-Muir 2017 (`[OOS backtest, contested]`).
2. **Learned label (a budgeted but validatable step up).** A coarse **2-3 state daily regime classifier** fit on the real broad history. This is the **one place the new data buys genuine, validatable capability** (see §3) — the classifier validates on abundant daily observations and clears the ~100-200-obs-per-regime stability bar easily.

**The hard ceiling on the RESPONSE (both tracks, same number):** whichever way you source the label, the response it drives — "in regime R, set the knob to v" — validates against the engine's *intraday exit outcomes* (~5-15 independent regime units), **not** against daily rows. So the response is capped at **≈1 learned parameter** (e.g. a single regime-contingent stop-width multiplier, OR a regime on/off gate, OR a regime-conditioned N-confirms — the user picks which one knob earns the budget).

> **Direction honesty (do not let this pass):** the measured intraday regime is mean-reverting, where Kaminski-Lo says stops are neutral-to-harmful. An honest regime→response therefore spends *most* of its time counseling restraint (stand down / widen), which is *safe and correct* but is the opposite of "more adaptive = more active." The "upside-capture" leg of any regime→response map is, on the measured data, a bet against the evidence.

### 1B. The acceptance-gate democracy — the governance that keeps 1A honest

The user's "democratized gate" idea maps cleanly onto existing machinery: it is **mostly a re-organization + one correctness fix of what already exists, plus ONE genuinely new layer.** The fatal anti-pattern the user named — *a discretionary score outvoting a failed overfitting veto* — dictates a strict two-stage partition.

**Stage 1 — HARD VETOES (sequenced first, structurally un-outvotable). These already exist:**

| Veto | Role | Code (verified file:line) |
|---|---|---|
| NN1 spec-freeze | Refuses to *start* a run whose spec contains a `BACKTEST_SELECTION` facet — fires pre-search, raises `RuntimeError` | `autotuner.py:1478-1582`, gate wiring `:1637-1640`, `:1680-1688` |
| BHY/Yekutieli haircut + `N_effective` | The real working permission-to-deploy gate: `_haircut_select` returns `winner=None` when no trial clears `p_adj ≤ HARVEY_LIU_FDR_Q` | `_haircut_select` `:1184-1272` (reject `:1268-1271`); `compute_n_effective` `:761-811` |
| Look-ahead / purge integrity | The per-fold series feeding the veto must be built on the purged validation fold or the whole significance computation is contaminated | fold construction `:2038-2047`; PURGE/EMBARGO usage `:2057-2075` |

**Stage 2 — DISCRETIONARY weighted panel (scores ONLY veto-survivors; the genuinely NEW layer).** Fixed, principled-constant weights — **never tuned** (a fixed-weight panel adds **zero** search-space DoF, so `N_effective` is unchanged and the panel cannot overfit by construction). The honest panel composition:

- **D2 — parameter-stability vs incumbent** (`−Σ|p_cand − p_incumbent|/scale`) and **D4 — prior-anchoring** (`−Σ|p_cand − p_prior|/scale`): the **honest backbone**. Pure parameter-distance brakes, **zero sample cost**, cannot overfit. They encode "burden of proof on deviation" — exactly what the forecast-combination literature says is right on thin samples.
- **D1 — cross-fold robustness** and **D3 — drawdown profile**: *directionally* right but **sample-starved**. Honest only in *coarse* forms; **D3 in precise CVaR form re-imports the rejected CVaR wall** (`project_cvar_divergence_validation_wall`) and should be coarse-directional or omitted.

**The load-bearing integration rule (this IS the enforcement of the user's forbidden anti-pattern):** the panel is a **ONE-DIRECTIONAL BRAKE.** It slots between the BHY-veto survivors and the final `baseline_decision` (`autotuner.py:2090-2107`). `panel_score` is `None` whenever any veto fails — so there is **no code path** where a panel score is even computed for a veto-failed candidate, let alone used to overturn the veto. The panel can only ever make the gate **STRICTER** (withhold an OOS-superior, veto-passing candidate whose robustness/stability profile is poor); it can **NEVER** resurrect a veto-failed one.

Decision logic (lexicographic, vetoes-dominant):
```
if not all_vetoes_passed:                                   KEEP_INCUMBENT / RESET_DEFAULT
elif candidate not OOS-superior:                            KEEP_INCUMBENT            # existing :2090 rule
elif panel_score(cand) >= panel_score(incumbent) + MARGIN:  ADOPT_CANDIDATE
else:                                                       KEEP_INCUMBENT            # panel withholds
```

**Two advisor producers map in; one is forbidden:** Overfitting Conscience stays **discretionary** (promoting it to a hard veto double-counts the `S`/`N_effective` evidence BHY already acts on). Spec Critic is structurally veto-*nature* but implemented advisory — **user decides** whether to promote it (Option O1). **Divergence Explainer must NOT be wired into the gate at all** — doing so resurrects the rejected CVaR-divergence detector; it stays INERT/operator-diagnostic-only.

---

## 2. LEARNED vs THEORY-SPECIFIED — and the honest parameter budget

This is the question the user most needs settled. The split, with the budget:

| Component | LEARNED or THEORY-SPECIFIED? | Validation unit | Honest budget |
|---|---|---|---|
| Reaction *curves* in 1A (vol-scaling, √-time) | THEORY-SPECIFIED (fitted nothing) | none — estimates nothing | unlimited (no DoF added) |
| Regime *label* (classifier) | **LEARNABLE** (or theory-fixed if you prefer ~0 DoF) | abundant **daily** observations | a coarse **2-3 state** classifier is validatable |
| Regime *response* (knob the label moves) | LEARNED | scarce **~5-15 independent regime units** | **≈1 parameter — full stop** |
| Discretionary panel weights | THEORY-SPECIFIED (fixed constants, never tuned) | none — adds zero search DoF | unlimited count, but each consumes the SAME thin sample for D1/D3 |
| Rich per-regime / per-symphony response vectors | would be LEARNED | ~5-15 regime units | **DATA-BLOCKED — exceeds the ≈1-knob ceiling** |

**Why the budget is ≈1 — three independent routes to one wall (the convergence that anchors this whole document):**
1. **Gate-1 consensus-weight DoF count:** ~5-15 independent regime units vs López de Prado's ≤1-param-per-independent-observation ceiling → ≈1 movable knob.
2. **adaptive-frontier regime-count side:** the *response* validates against ~5-15 independent regime units regardless of how many daily rows the *classifier* sees → ≈1 response parameter.
3. **pillar3 empirical collapse:** episode-level Guard-Alpha t=2.25 → day-clustered t=1.52 (not significant) — the data itself demonstrates the regime-not-rows collapse.

Three different derivations, three different literatures, **one number.** This is not an echo: I verified that adaptive-frontier reached its ≈1-knob figure from the regime-count direction independently of Gate-1's DoF-count derivation. When that happens, the number is load-bearing, and I state it without hedging.

**The honesty seam the panel makes free (no new producer code):** store the THEORY prior as a frozen content-hashed `spec_facet`; the TUNED current value lives in per-symphony params. "Still on prior vs moved" then becomes visible through the *existing* advisors (Spec Critic BREACHes on `BACKTEST_SELECTION`; Overfitting Conscience tracks the added DoF) surfaced to the read-only `/ai-advisor` route — so the system can *show* the operator that it declined to move a knob, which is the governance artifact that makes "safe-by-degeneracy" legible rather than invisible.

---

## 3. The real role of the new `external_data` daily panel

**What it IS:** a **DAILY** panel — `[ticker, date, adj_close, daily_return, source1, source2, confidence]`. **No OHLC, no volume, no intraday bars, no VWAP inputs.** ~140-year span (1885→2026 via synthetic back-fill), broad cross-section (~12,934 tickers).

**What it ENABLES (two genuine, honest uses):**
1. **Fitting + OOS-validating the coarse daily regime classifier** of §1A/§2. This is the one place the new data buys *real, validatable* capability — the classifier's validation unit is daily observations, of which there are plenty (clears 100-200-obs-per-regime easily, even after excluding synthetic rows and restricting to ~10yr+ tickers).
2. **Backtesting the acceptance-gate DECISION LOGIC across many more historically distinct regimes than the live era contains.** Highest-value use: **anti-pattern regression** — construct an adversarial candidate that *fails* the overfitting veto but scores *high* on the discretionary panel, then assert the gate returns `KEEP_INCUMBENT` — across real crises (1929, 1973-74, 1987, 2000-02, 2008, 2020, 2022). This operationalizes the user's forbidden anti-pattern as a falsifiable test on real history. It also lets you *count* the independent drawdown regimes the data actually contains (the binding constraint on how many discretionary criteria the panel can honestly carry).

**What it CANNOT do (the hard ceiling, stated unhedged):**
- **It cannot replay the exit engine.** The engine is *intraday* (trailing-stop ticks, VWAP breakdown/bleed, intraday √-time squeeze); the panel is *daily* with no intraday bars. Any "exit-rule" run on it is a **daily proxy, not Planet Stopper** — validating the proxy is not validating the engine.
- **It cannot manufacture intraday-regime validation budget.** Breadth (12,934 tickers) widens the *cross-section per day*; it does **not** multiply the independent *time/regime* units the exit-response validation rests on. The ~5-15-regime wall stands.

**Mandatory data-prep (both tracks agree — same rules):**
- `is_synthetic_row = (source1 == 'synthetic') OR (source2 == 'synthetic')` — the only synthetic signal available (`ticker_metadata` has no leverage/asset-class flag).
- `inception_date(ticker) = min(date WHERE NOT is_synthetic_row)`.
- **EXCLUDE synthetic pre-inception history from any ground-truth validation split.** The ~7% synthetic rows are concentrated in leveraged/inverse ETFs (UPRO modeled to 1885, TQQQ to 1995) — they are *model output, not observations*. Validating against them validates the synthesizer's model, not the market. Non-negotiable on both tracks.

> **The danger to name explicitly:** mistaking "the gate logic behaved correctly across 140 years of daily regimes" or "the daily classifier validated" for "the tuned exit strategy is validated." These are different claims; only the first two are supported by this data.

---

## 4. What remains DATA-BLOCKED — and exactly what would unblock it

**DEFINITIVE answer on the binding gap (intraday depth):** decades-deep, broad-universe **intraday** data does **NOT** exist at the depth or freeness of the daily series, and not matched to this engine. `[High]`, vendor-primary:
- Deepest broad-universe intraday anywhere: **~1993** tick-by-tick (TickData/TAQ) / **~2004** one-minute bars — i.e. **~20-30 years, paid, not ~140**, and **not matched to these symphonies' historical holdings or the engine's minute cadence**.
- The daily 1885→2026 depth **cannot be ported to the intraday horizon the engine actually trades.** The order-of-magnitude gap (decades vs ~century) is structural.

| Capability | Status | What would unblock it |
|---|---|---|
| Coarse 2-3 state daily regime classifier | **ACHIEVABLE NOW & validatable** | (already unblocked by the daily panel) |
| ≤1 theory-anchored learned response knob, FDR-gated | **ACHIEVABLE, budget-capped at ≈1** | (achievable now within budget) |
| Rich learned regime→exit-response (per-regime vectors, per-symphony, learned curves) | **DATA-BLOCKED** | More **independent intraday regime episodes** — accruable only by forward live/paper at ~252 days/yr, i.e. **years**, not a dataset purchase |
| OOS validation of the upside (Guard-Alpha-positive) leg | **DATA-BLOCKED + currently contraindicated** | Same: years of live/paper accumulation; the measured regime is presently unfavorable |
| Tail-risk-conditioned (CVaR-driven) regime gating | **DATA-BLOCKED — already rejected twice** | ~1,000 tail obs vs ~6-37 available; do not re-litigate (`project_cvar_divergence_validation_wall`) |

**The honest unblock, stated plainly:** the *classifier* side could be marginally sharpened by purchasing ~20-30yr intraday data (a real but costly gain), but the **exit-response** validation is unblocked **only by forward live/paper accumulation** — the README's own prescribed path. Years of live regime units, not a data purchase, move the response needle. There is no dataset you can buy that escapes the ~5-15-regime wall for the exit-decision question.

---

## 5. Recommended next step + open questions for the user

### Recommended next step (a phased, honest build — sequencing only; Gate-2 owns the decomposition)
1. **Fix H-1 first** (`autotuner.py:1251` — call `tstat_fn`, not hardcoded `compute_sortino_tstat`). This is a **live defect**, fixable independently, and it is the precondition for *any* veto/permission decision to mean anything. An incorrect primary veto is as dangerous as an outvotable one. Do this regardless of whether the rest proceeds.
2. **Build the acceptance-gate democracy (§1B)** — Stage-1 vetoes already exist; the new codepath is the fixed-weight one-directional-brake panel (D2 + D4 backbone). Ship the **anti-pattern regression** (§3) as the headline acceptance test, run across real historical crises on the daily panel.
3. **Add the regime-response layer (§1A)** — start with the **theory-fixed** label (~0 DoF) for the first cycle; optionally graduate to the **learned 2-3 state daily classifier** validated on the real broad history, moving **exactly one** theory-anchored response knob under the existing FDR/permission gate.
4. Treat the whole thing as **safe-by-degeneracy**: the expected behavior is "keep the incumbent / stay on prior / counsel restraint," and that degeneracy is precisely what keeps it honest. Anyone who needs to *see* it adopt new tunings often will be tempted to loosen the veto or let the panel promote — both break the honesty.

All of layers 2-3 are new codepaths → **Agent-Teams TDD** (per project CLAUDE.md hard requirement); H-1 is a one-line correctness fix to an existing codepath (and is covered by the standard exception, but its blast radius onto the live canonical THEORY-bundle path means it still wants a golden-fixture test per the math-layer standard).

### Open questions for the user (genuine WHAT decisions research cannot settle)
1. **Direction-of-response honesty.** Do you accept that an honest regime→response will *most often counsel restraint* (stand down / widen) on the measured mean-reverting intraday regime — i.e. "more adaptive" will usually mean "less firing," not more? (Same confront-up-front issue as Gate-1 OQ-3.)
2. **Which single knob earns the ≈1-parameter response budget?** A regime-contingent stop-width multiplier, a regime on/off gate, or a regime-conditioned N-confirms? (Interacts with the Gate-1 consensus design.)
3. **Classifier: theory-fixed (~0 DoF) or learned (budgeted but validatable on daily obs)?**
4. **Spec Critic: promote to hard structural veto, or leave advisory?** (Option O1 — changes deploy behavior.)
5. **Panel composition:** lean (D2 + D4 only, maximally honest) vs full (D1-D4, richer but D1/D3 sample-starved and D3-precise re-imports the CVaR wall)?
6. **Panel directionality:** one-directional brake (honest-by-construction) vs bi-directional (can promote a near-miss — more "adaptive-feeling" but widens the gate = the documented overfitting road)?
7. **Intraday-depth acquisition appetite:** is paid ~20-30yr intraday data worth buying *for the classifier side only*, knowing it does NOT unblock the exit-response validation?
8. **`MARGIN` constant basis:** what principled value sets the panel's score hurdle (analogous to the FDR `q` choice)?

> **One item that is NOT an open question but a Gate-2 acceptance criterion (so it is not lost):** a fixed-weight panel adds **zero** search-space tunables → `N_effective` unchanged → veto bar unchanged. This is the panel's core safety property and the reason it cannot overfit. If any future variant introduces a *tunable* panel weight, that weight MUST be charged into `compute_n_effective` (Gate-1 §2.3 hinge) — non-negotiable.

---

## 6. Cross-verification & down-ranking ledger (adversarial audit trail)

**Verified by my own direct read (promoted to FACT):**
- H-1 at `autotuner.py:1251` — hardcoded `compute_sortino_tstat`, `tstat_fn` ignored, `_crra_gamma` discarded (`:1243`). Both researchers correct.
- Reactive layers at `math_engine.py:294-372` — √-time squeeze is literally zero-free-parameter THEORY (comment-confirmed); inverse-vol stop scaling confirmed. adaptive-frontier's "already reactive at ~0 DoF" claim correct.

**Triple-confirmed, stated without hedging:** the **≈1-knob honest budget**. Reached independently by (1) Gate-1 DoF count, (2) adaptive-frontier regime-count side, (3) pillar3 empirical t=1.52 collapse. Three literatures, one number — the spine of the recommendation.

**Cross-track agreement confirmed (no tension to reconcile):**
- Both tracks quote the **same independent-regime count (~5-15)** and the **same 7%-synthetic-exclusion rule** — the alignment item each flagged as an open coordination point resolved *consistently* (one budget, not two). I verified the two deliverables do not double-count the sample.
- Both reach **safe-by-degeneracy** independently (gate side: incumbent-default; frontier side: restraint-default) — same property viewed from governance vs strategy angles.

**Down-ranked / labeled-interpretation (NOT promoted to fact):**
- **Moreira-Muir vs Cederburg vol-management conflict** — shown, NOT adjudicated. Moreira-Muir 2017 (`[OOS, contested]`) says vol-scaling works; Cederburg 2020 (`[OOS, High]`) says it mostly fails OOS except MOM/ROE/BAB; Barroso-Detzel adds it dies on transaction costs. The favorable evidence is *off-horizon, off-instrument* (monthly factors, not intraday stops) — the transfer is not licensed. Carried as a contest, not a conclusion.
- **100-200-obs-per-regime HMM stability threshold** — `[Medium, practitioner/textbook tier, single-source for the exact number]` (MetricGate/MDPI). Carried as flagged-for-statistician, not asserted. Does not change the conclusion: even a conservative threshold is cleared by the daily series for a 2-3 state classifier.
- **Breadth/ADX-multiplier regime filter** — `[Folklore — high adoption / low evidence]`. May inform curve *shape*; never cited as proof.
- **CPCV as co-prerequisite for D1 cross-fold robustness** — `[interpretation]`, named-but-unimplemented (`autotuner.py:368-370`); carried as a flagged dependency, not a settled fact. CPCV improves selection-variance accounting; it does NOT manufacture independent regimes.
- **`external_data` schema facts** — both researchers labeled these *relayed/verified-by-another-agent* (neither decompressed the .gz themselves). I carry the schema as relayed, not independently confirmed; the data-prep rules built on it are sound *conditional on* that schema being accurate. **Flagged as the one input not verified to source within this team.**

**Citation integrity:** every concrete file:line in §1-§5 traces to one of the two research deliverables, the prior Gate-1/audit knowledge base, or my own direct read (§ preamble). Where a claim is interpretation or relayed, it is labeled. No §1-§5 conclusion rests on an uncited assertion.
