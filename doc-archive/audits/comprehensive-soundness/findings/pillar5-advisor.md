<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. AI Advisor findings: Divergence Explainer call-site gap (S3-AUDIT-002) closed in Sprint 3 fix cycle; advisor naming honesty concerns informational. See DECISIONS.md DE-S3-001/002. -->
# Pillar 5 — AI Advisor: As-Built Audit + North-Star Feasibility Verdict

**Auditor:** advisor-auditor (audit-soundness team)
**Date:** 2026-05-30
**HEAD:** 8586ab2 (worktree `audit-soundness`)
**Scope:** PART A — audit the three existing advisor producers as reality. PART B — rigorous feasibility/soundness verdict on the aspirational "real advisor" north star.
**Evidence standard:** file:line for as-built; cited methodology for feasibility.
**Stance:** honesty over encouragement. Fact / Interpretation / Options-not-recommendations separation enforced per researcher charter.

---

## PART A — AS-BUILT (the three producers as REALITY)

### A.0 Wiring verification (file:line) — CONFIRMED

All three producers are imported and invoked from `autotuner.py`, post-walk-forward, on the **nightly autotune path** (NOT the 1-minute execution path — architecture constraint #1 respected):

- Imports: `autotuner.py:15-17` (`_oc`, `_sc`, `_de`).
- **Spec Critic** — `autotuner.py:1704-1707`. Called **once per bundle**, *before* the per-symphony loop, right after the NN1 hard-gate (`validate_nn1_compliance`, `:1681`). Reads `spec_facets` via `database.advisor_ro_query` (`:1694-1699`). `symphony_id=None` at this site by design (`:1700-1703` inline note: normalized_name not yet available pre-loop).
- **Overfitting Conscience** — `autotuner.py:2202-2205`. Called **per symphony**, immediately after `save_autotune_run` returns `_inserted_id` (`:2185-2186`). Reads `researcher_dof_ledger` rows (`:2179-2184`) and `prior_runs` (`:2195-2199`) via `advisor_ro_query`.
- **Divergence Explainer** — `autotuner.py:2209-2212`. Called per symphony, post-OC mirror, `cvar_row=None` (forces the producer to either no-op or self-fetch).
- All three are wrapped in `try/except … logging.warning(... "advisory only" ...)` (`:1706-1707`, `:2204-2205`, `:2211-2212`). **Fact:** an advisor failure cannot break the autotune run. This is correct fail-safe design for an advisory layer.

**Wall integrity (Fact):** every DB read in all three modules routes through `database.advisor_ro_query`; no module opens a connection directly. The docstrings cite a CI lint test (`test_advisors_module_uses_advisor_ro_query`) enforcing this. Verified by reading: the only `database.*` calls are `advisor_ro_query` (reads) and `insert_advisor_observation` (writes).

### A.1 Overfitting Conscience — `advisors/overfitting_conscience.py`

**What it actually computes (Fact, `:47-176`):**
A pure function over three scalars pulled from the just-saved `autotune_runs` row — `n_effective`, `s_count` — plus `researcher_dof_ledger` rows filtered to `evidence_source == "BACKTEST_SELECTION"` for the run's `spec_bundle_id`. Three indicators:
- **I-1** (`:96-105`): `S = max(Σ n_configs_searched over BACKTEST_SELECTION ledger rows, stored s_count)`. Any `S > 0` → at least WATCH.
- **I-2** (`:109-130`): `ratio = S / N_optuna`, where `N_optuna = max(N_effective − S, 1)`. `ratio > 0.10` (`S_RATIO_BREACH_THRESHOLD`, `:40`) → BREACH.
- **I-3** (`:112-135`): monotonically increasing `s_count` across ≥2 prior same-symphony runs → floor at WATCH.

**Soundness verdict: SOUND, non-circular, genuinely analytical — but narrow and mostly redundant with an existing hard gate.**
- It is **non-circular**: it reads counters (`S`, `N_effective`) that are produced upstream by the BHY haircut accounting in `autotuner.py` (`compute_n_effective`, additive `N_eff = N_optuna + S`). It does not re-derive its own verdict from the same fit it is judging. (Interpretation: this is the correct shape for an overfitting monitor — it audits the *degrees-of-freedom budget*, not the backtest score.)
- **The one threshold it owns (0.10) is un-derived.** `S_RATIO_BREACH_THRESHOLD = 0.10` cites "council synthesis §2.5; Sprint 3 dispatch brief" (`:38-40`) — i.e., an internal design doc, **not a published calibration source**. Grade: `[Folklore — internal-mandate / no external evidence]`. This is consistent with the project's own honesty about un-anchored thresholds (vision §6.3) but should not be presented to the operator as a statistically meaningful cutoff.
- **Largely redundant with NN1.** The autotuner *already refuses to start* if any load-bearing facet is `BACKTEST_SELECTION` (`autotuner.py:1681-1688`, RuntimeError). So in normal operation `S` from facets is forced to ~0 by the hard gate before OC ever runs. OC's added value is therefore concentrated in (a) the I-3 drift signal across runs and (b) catching ledger-recorded offline hand-selection (`S` counter) that NN1's facet check doesn't cover. (Interpretation: OC is a real signal, but its BREACH branch is mostly unreachable given the upstream hard gate — its practical role is the softer WATCH/drift telemetry.)

**Empirical Evidence:** `[Theoretical]` — the construct (policing selection DoF) is grounded in the multiple-testing / deflated-Sharpe literature ([Bailey & López de Prado 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)). The specific 0.10 ratio is unvalidated.
**Replication Status:** N/A (deterministic accounting function, not a statistical estimator).
**Regime Sensitivity:** none — it does not touch market data; it audits the optimization budget.

**Cosmetic vs genuine:** **Genuine** (it computes a real DoF-budget signal), with the caveat that its strongest verdict (BREACH) is shadowed by a stricter upstream hard gate.

### A.2 Spec Critic — `advisors/spec_critic.py`

**What it actually computes (Fact, `:89-202`):** A pure structural lint over `spec_facets` rows:
- **I-1** (`:124-126`): the three Phase-1 THEORY facets (`gamma`, `utility_family`, `wealth_argument`) must be present; any missing → BREACH.
- **I-2** (`:128-132`): every facet's `freeze_discipline` must be in the six-value allow-set (`:75-82`); default-deny on anything else (incl. `BACKTEST_SELECTION` and unknown values) → BREACH.
- **I-3** (`:134-152`): any facet `frozen_at` ≥ 90 days old (`SPEC_AGE_WATCH_THRESHOLD_DAYS`, `:69`) → WATCH.
- **I-4** (`:154-155`): any Phase-2 facet name present (`lambda`, `hysteresis-threshold`) → BREACH ("phase scope leak").

**Soundness verdict: SOUND and non-circular, but it is a config linter, not a quant signal.**
- It is **non-circular and deterministic** — pure structural validation of metadata. No market data, no fit.
- **It overlaps heavily with NN1** (`validate_nn1_compliance`, `autotuner.py:1681`). I-2 (BACKTEST_SELECTION rejection) duplicates the NN1 hard gate that already runs ~10 lines earlier. (Interpretation: Spec Critic is the *soft/advisory* restatement of NN1 plus completeness/age/phase-leak checks. Its unique value is I-1 completeness, I-3 age, I-4 phase-leak — none of which NN1 enforces.)
- **The 90-day age threshold is un-derived** (`:66-69`, cites the dispatch brief). Grade: `[Folklore — internal-mandate]`. It is harmless (WATCH-only, non-blocking) but is not a calibrated staleness science.
- **Honesty note:** `freeze_discipline` allow-set is *default-deny* (`:71-82`) — a good forward-compat safety posture (an unknown discipline BREACHes rather than silently passing).

**Empirical Evidence:** `[Theoretical]` — the discipline taxonomy traces to the project's NN1 spec-freeze design; no external validation applies to a config linter.
**Replication Status:** N/A.
**Regime Sensitivity:** none — touches no market data.

**Cosmetic vs genuine:** **Genuine as a governance/lint check.** It is NOT an analytical risk signal and should never be presented as one. Its job is to keep the spec bundle honest; it does that.

### A.3 Divergence Explainer — `advisors/divergence_explainer.py`

**What it actually computes (Fact, `:65-141`):**
- When `SECOND_WINDOW_CVAR_ENABLED` is off (default; resolved at `:165-167`): writes a single `NOT_APPLICABLE` row with `raw_response={"feature_flag":"off"}` (`:99-109`).
- When on: writes one `INFORMATIONAL` row carrying **two independent CVaR window values** (`short_window_cvar_pct`, `long_window_cvar_pct` + their tail-obs counts) (`:111-141`), pulled from a `cvar_diagnostics` row.
- **Hard architectural constraint enforced (Fact, `:7-18`, `:111-112`, `:124-131`):** the module is structurally forbidden from emitting any signed divergence, difference, ratio, spread, or threshold between the two windows. It carries the rejected-idea decision (`project_cvar_divergence_validation_wall`) into every row by *construction* — there is no code path that subtracts the two windows.

**Soundness verdict: SOUND by design, but currently INERT in production — it is a faithful tombstone, not a live signal.**
- **It is dead in practice (Fact).** Cross-checked the producer of its inputs: `cvar_5pct_long` / `cvar_n_tail_long` are written as **hardcoded `None`** on both `math_engine.py` CVaR-record paths (`math_engine.py:1456-1457`, `:1544-1545`), and are only ever populated in `alpha_bot_execution.py:1586-1605` **inside an `if os.environ.get("SECOND_WINDOW_CVAR_ENABLED","0")=="1"` guard**. With the flag off (default, `:1588`), the long window is never computed, so even if the explainer's `INFORMATIONAL` branch ran it would surface `None`/`None`. In the default configuration the Divergence Explainer writes only `NOT_APPLICABLE` audit-trail rows.
- **This is correct and intentional**, not a defect. It is the *sole surviving residue* of an idea the decision-science council REJECTED twice (`project_eut_cvar_migration_council_verdict`, `project_cvar_divergence_validation_wall`). The module's value is **negative-space governance**: it makes the rejection executable and prevents a future contributor from quietly reintroducing a divergence trigger (the forbidden-keys list at `:15-17` is a guardrail, though note it is a *documented convention*, not a runtime assertion — nothing in the code actively rejects a forbidden key; the protection is that no code path computes one).

**Empirical Evidence:** `[Theoretical]` for the underlying CVaR estimate; the divergence signal it deliberately does NOT compute was assessed `un-validatable` by the project's own council (the ~5-15 independent regime-shift events over 3 years wall, per the decision log).
**Replication Status:** N/A — the live signal does not exist by design.
**Regime Sensitivity:** the suppressed signal would have been *least reliable exactly at regime shifts* (the same wall as the MC kNN gate) — which is the documented reason it was rejected.

**Cosmetic vs genuine:** **Neither cosmetic nor a live signal — it is a governance tombstone.** Honest assessment: in the default config this producer's only observable output is a `NOT_APPLICABLE` row. Calling it an "AI Advisor producer" overstates it; it is a feature-flagged, currently-off CVaR display hook with a hard wall against the rejected divergence idea.

### A.4 Part A summary

| Producer | Computes | Sound? | Circular? | Genuine signal vs cosmetic | Key caveat |
|---|---|---|---|---|---|
| Overfitting Conscience | DoF-budget ratio `S/N_optuna` + drift | Yes | No | Genuine (DoF audit) | BREACH branch shadowed by NN1 hard gate; 0.10 threshold un-derived |
| Spec Critic | Structural lint of spec facets | Yes | No | Genuine (governance lint), NOT a quant signal | Heavy overlap with NN1; 90-day threshold un-derived |
| Divergence Explainer | Two CVaR windows side-by-side (flag-gated) | Yes | No | Governance tombstone; INERT by default (writes NOT_APPLICABLE) | Long window never computed unless `SECOND_WINDOW_CVAR_ENABLED=1` |

**Overall Part A (Interpretation):** All three are **honest, non-circular, and correctly fail-safe**. None is fraudulent or cosmetic-in-disguise. BUT — collectively they are **governance/meta-analysis instruments** (overfitting-budget audit, spec lint, rejected-idea tombstone). **None of them is the "advisor" the user's north star imagines** (de-correlation, asset add/swap, logic suggestions, chat). There is a large gap between what `advisors/` is (a Phase-1 spec/overfitting conscience layer) and what the user wants (a portfolio strategist). The naming "AI Advisor producers" invites the reader to expect the latter; the reality is the former. **This naming gap is the single most important as-built finding.** There is also an unrelated `migrations/003_llm_suggestions.sql` table present — an apparent vestige of an earlier LLM-suggestion concept — which none of the three current producers populate.

---

## PART B — FEASIBILITY OF THE ASPIRATIONAL "REAL ADVISOR" NORTH STAR

The user's four north-star capabilities do not exist. For each I give a verdict in three tiers:
**(S) sound and buildable · (U) buildable but its OUTPUT would be statistically untrustworthy · (P) pipe dream on this stack/data.**

### B.0 The data the stack actually has (Fact — load-bearing for all four)

- **No first-class daily per-symphony return table.** Grepped `database.py` for `daily_returns`/`return_series`/`portfolio_returns` → **no matches**. The closest persisted return data is `shadow_history` (`migrations/008_shadow_history.sql`): per-cycle `current_return` + `shadow_return` per `symphony_id` per `trading_day`. A daily close-to-close per-symphony series is *derivable* from this (last cycle of each trading_day) but is not stored as such.
- `synthetic_history.py` can fetch **125 trading days** of live Alpaca history per the autotuner contract; more is fetchable but the project's standing calibration window is 125 days (~6 months).
- The operator runs a **small number of symphonies** (the daemon monitors "one or more symphonies"; the project's working scale is a handful, not hundreds).
- **CVaR/MC machinery already documents a hard data wall** (decision log): tail/regime-dependent estimates need ~1,000 tail-relevant obs; 125 days yields ~6 tail days, 3 years ~37. This wall is inherited by anything tail- or regime-conditioned below.

### B.1 Portfolio-level de-correlation analysis (correlation risk across symphonies)

**Verdict: (S→U) — the *mechanics* are sound and buildable; the *trustworthiness of the output* degrades sharply exactly when it matters, and depends on how many symphonies are correlated against how much history.**

**What it would require (Fact + cited method):**
- A daily return matrix: `T` days × `N` symphonies. Derivable from `shadow_history` (long way) or backfilled from Alpaca (`synthetic_history.py`).
- A covariance/correlation estimator. The naive sample covariance is **unreliable when `T` is not >> `N`** — the matrix is noise-dominated and, for `T ≤ N`, singular ([curse-of-dimensionality / high-dim covariance literature](https://arxiv.org/pdf/1201.4672)). The standard fix is **Ledoit-Wolf shrinkage** ([Ledoit & Wolf, "Honey, I Shrunk the Sample Covariance Matrix", SSRN 433840](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=433840)), which blends the sample matrix toward a constant-correlation target with an analytically optimal, free-parameter-free coefficient.

**Where it is genuinely sound:** with a *small* `N` (a handful of symphonies) and `T = 125` days, you are NOT in the singular regime (`T >> N`). A shrinkage-estimated correlation matrix among a few symphonies is a **defensible, buildable diagnostic.** This is the favorable case and the most achievable of the four.

**Where the output becomes untrustworthy (the honest part):**
- **Correlations are regime-dependent and rise toward 1 in exactly the left-tail crisis episodes where de-correlation is the whole point.** This is one of the best-documented stylized facts in the literature ([Page & Panariello, "When Diversification Fails", Financial Analysts Journal 2018](https://www.tandfonline.com/doi/full/10.2469/faj.v74.n3.3); the "correlations go to 1 in a crash" result). A full-sample correlation matrix will *systematically understate* the correlation risk the operator most needs warned about. To capture tail co-movement you need **conditional/tail correlation or regime-switching estimators** ([regime-switching & local Gaussian correlation, arXiv:2306.15438](https://arxiv.org/pdf/2306.15438)) — and **these re-import the same regime-event data wall** the decision log already documents: tail-conditional correlation among `N` symphonies is estimated from the same scarce ~5-15 independent regime-shift episodes that killed the CVaR-divergence detector.
- If `N` ever grows toward the size of `T/some-factor`, the estimate collapses into the curse-of-dimensionality regime even with shrinkage.

**Empirical Evidence:** Ledoit-Wolf `[Out-of-sample backtest — replicated]` (widely replicated; GMV+LW is a standard baseline). Tail-correlation breakdown `[Stylized fact, strong literature consensus]`.
**Replication Status:** LW shrinkage — yes, extensively. Tail-correlation-rises-in-crisis — yes, pervasive across asset classes.
**Regime Sensitivity:** **This is the whole problem.** The unconditional version is reliable in calm regimes and *misleading in crises*; the conditional version that would fix that is starved of the independent tail observations needed to estimate it.

**Options + trade-offs (NOT a recommendation):**
- *Option 1 — unconditional shrinkage correlation as an operator diagnostic*, clearly labeled "calm-regime estimate; understates crisis co-movement." Sound, buildable, honest. Trade-off: gives a false sense of diversification precisely in a crash.
- *Option 2 — add a tail/regime-conditional overlay.* More honest about crisis risk, but the estimate carries a wide error bar from the scarce regime-event count; same wall as the rejected CVaR-divergence work. Trade-off: risks presenting an untrustworthy number as if precise — the exact failure mode the project already legislated against.

### B.2 Suggesting assets to ADD (e.g. IALT) or SWAP OUT

**Verdict: (U) for a quantitative recommendation; (P→U) for an autonomous one. Buildable as a *candidate-generator with disclaimers*; NOT buildable as a trustworthy "add IALT" instruction.**

**What it would require (Fact + cited method):**
- Out-of-universe candidate data (IALT etc.) — fetchable via Alpaca.
- A selection objective: marginal contribution to portfolio risk/return, marginal diversification (correlation/beta to the existing book), or an optimizer (e.g., min-variance with the candidate added). All of these consume the **same covariance estimate** as B.1 — so they inherit B.1's reliability ceiling *and amplify it*: portfolio optimizers are notoriously sensitive to covariance estimation error (error-maximizers), which is the original motivation for shrinkage.
- A selection step over many candidate assets = **a multiple-testing search.** Picking "the asset that most improves the backtested portfolio" across a candidate universe is precisely the selection-bias setup that inflates apparent performance ([Bailey & López de Prado, Deflated Sharpe Ratio, SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)). The effective number of independent trials must be deflated; with 125 days of history the deflated significance bar is very high and most "winning" additions will not clear it. (This is the *same* discipline OC and the BHY haircut already enforce on the exit-engine parameters — it applies with full force here too.)

**Why it's untrustworthy, not impossible:** the system *can* compute "asset X reduces estimated portfolio variance by Y% over the last 125 days." That number is real arithmetic. But (a) it rests on a noise-dominated covariance estimate, (b) it is an in-sample selection from a candidate search → overfit by construction, and (c) 125 days cannot validate that the diversification persists out-of-sample. An honest version emits **ranked candidates with explicit "in-sample, unvalidated, regime-fragile" labeling**; a dishonest version emits "add IALT."

**Empirical Evidence:** `[Backtest-only, in-sample]` for any concrete suggestion; the overfitting hazard is `[Theoretical, strongly established]`.
**Replication Status:** the *hazard* (selection bias inflates backtested asset-selection) — extensively replicated. A *trustworthy* short-history asset-selection signal — Unknown / no evidence it exists at this data scale.
**Regime Sensitivity:** diversification benefit estimated in one regime routinely vanishes in the next (B.1 tail-correlation result).

**Options + trade-offs:** a *decision-support candidate ranker* (sound-ish, honest, operator decides) vs an *autonomous suggester* (pipe dream at trustworthiness — it would confidently emit overfit picks). Note the project's stated identity is an **exit-only overlay that "never picks symbols, never sizes anything"** (vision §1) — asset add/swap is a categorical scope expansion beyond the product's charter, not just a feature.

### B.3 Suggesting logic changes per symphony

**Verdict: (P) pipe dream as an automated, trustworthy capability on this data; (U) at best, as a human-in-the-loop hypothesis generator that must then go through the existing walk-forward + BHY gate.**

**Why (Fact + cited method):**
- "Suggest a logic change" = propose a new strategy variant and claim it is better. Validating that claim is a backtest. The project's **own walk-forward already self-reports a ~4-usable-day validation fold** (`autotuner.py:360-377`, per vision §4) and the code itself states the per-trial t-stat is "not defensible" at that length. A logic-change suggester would be generating *new* hypotheses to test against the *same* thin window.
- Every distinct logic variant multiplies the trial count. [DSR / probability-of-backtest-overfitting work](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) shows that searching over windows × thresholds × entry/exit logics explodes the effective test count (the canonical "10×5×3 = 150 configs" example), and the minimum backtest length to support that many trials is far beyond 125 days. An advisor that proposes logic changes is an **overfitting engine** unless every suggestion is funneled through a deflated, out-of-sample gate — and the data to run that gate credibly does not exist here.
- This is the **identical wall** the project already hit and legislated around twice (EUT+CVaR migration rejected; CVaR-divergence rejected; both `do-not-re-litigate`). A logic-change advisor walks straight back into it.

**Empirical Evidence:** `[Theoretical / strongly established hazard]`. No evidence a short-history automated logic-suggester produces out-of-sample-robust changes.
**Replication Status:** the overfitting hazard — replicated; a trustworthy automated logic-improver at 125 days — no evidence it exists.
**Regime Sensitivity:** maximal — a logic tuned on the calibration regime is tuned to the wrong world if the regime shifts (vision load-bearing assumption #4).

**Options + trade-offs:** *human-in-the-loop hypothesis prompt* ("here are patterns in your exits worth investigating") that the operator then validates through the existing autotuner gate (honest, but slow and still data-wall-bound) vs *automated logic recommender* (pipe dream — it will confidently overfit). There is no middle path that is both automated and trustworthy at this data scale.

### B.4 Chat-like advisor interaction (LLM)

**Verdict: (S) for the *interaction surface*; (U/P) for the *substance* of any quantitative claim it makes. Buildable as a UI; dangerous as an authority.**

**Why (Fact + cited method):**
- A chat wrapper over the existing DB (advisor_observations, shadow_history, cvar_diagnostics) is **straightforwardly buildable** — RAG over the operator's own data, natural-language Q&A about *what the engine did and why*. As an *explanatory/retrieval* surface over already-computed, already-caveated numbers, this is sound and the most achievable substance-bearing item after B.1's calm-regime diagnostic.
- The danger is the moment the chat is asked for **advice** (add this, change that, you're over-exposed). Then it inherits B.1-B.3's untrustworthiness AND adds LLM-specific failure modes:
  - **Hallucination of plausible-but-wrong specifics**, especially numeric/regulatory ([LLM hallucination in financial institutions, BizTech 2025](https://biztechmagazine.com/article/2025/08/llm-hallucinations-what-are-implications-financial-institutions); [hallucination detection survey, arXiv:2601.09929](https://arxiv.org/html/2601.09929v1)).
  - **Reinforcing the operator's existing biases** — a peer-reviewed result that LLMs *amplify* private-investor biases and *increase* portfolio risk ([Biased echoes, PLOS One 2025 / PMC12204588](https://pmc.ncbi.nlm.nih.gov/articles/PMC12204588/)).
  - The literature consensus is that LLM investment value is realized **only under substantial human oversight** ([LLMs and stock investing, arXiv:2603.19944](https://arxiv.org/pdf/2603.19944)) — i.e., not as an autonomous advisor.

**Empirical Evidence:** chat-as-explainer `[buildable, low-risk]`; chat-as-advisor `[Live evidence of harm]` — the bias-amplification finding is an actual empirical study, not theory.
**Replication Status:** LLM hallucination + bias-amplification — yes, growing replicated literature.
**Regime Sensitivity:** an LLM has no inherent regime awareness; it will speak with equal confidence in calm and crisis.

**Options + trade-offs:** *explain-only chat* strictly bounded to retrieving + narrating the engine's own caveated outputs (sound; aligns with the deferred "Narrator" role in the project's own roadmap) vs *advice-giving chat* (buildable UI, but emits authoritative-sounding claims that are untrustworthy at this data scale and empirically bias-amplifying). The hard line is explain vs advise.

### B.5 Part B summary table

| North-star capability | Verdict | Buildable? | Output trustworthy? | Binding wall |
|---|---|---|---|---|
| 1. Cross-symphony de-correlation | **S → U** | Yes (shrinkage) | Only in calm regimes; understates crisis | Tail-correlation rises in crisis; regime-event scarcity |
| 2. Add/swap asset suggestion | **U** (P if autonomous) | Yes (ranker) | No — in-sample selection over noisy covariance | DSR multiple-testing + 125-day validation wall; also out-of-charter |
| 3. Per-symphony logic-change suggestion | **P** (U only human-in-loop) | Mechanically yes | No | ~4-day validation fold; overfitting; same wall rejected twice already |
| 4. Chat-like advisor | **S** (explain) / **U-P** (advise) | Yes (UI) | Explain: yes. Advise: no | LLM hallucination + empirically bias-amplifying; data walls of 1-3 |

---

## Bottom line (honest, not encouraging)

**Part A:** The three existing producers are real, sound, non-circular, and correctly fail-safe — but they are **governance/meta instruments** (overfitting-budget audit, spec lint, a rejected-idea tombstone), **not the portfolio-strategist "advisor" the north star describes.** The Divergence Explainer is **inert by default** (writes only `NOT_APPLICABLE`). Calling this layer "AI Advisor" oversells it relative to what it computes.

**Part B:** The north star is **not uniformly a pipe dream — it is sharply tiered.**
- The **only piece that is both buildable and honestly useful today** is a **calm-regime cross-symphony correlation diagnostic** (B.1, via Ledoit-Wolf) and an **explain-only chat** over the engine's own caveated outputs (B.4). Both are operator *diagnostics*, consistent with the project's established "diagnostic-not-trigger" posture.
- **Asset add/swap (B.2)** and **logic-change suggestions (B.3)** run straight into the **same data-sufficiency walls the decision log already documents twice** (EUT+CVaR and CVaR-divergence rejections): selection bias under multiple testing + a ~125-day validation window that cannot certify out-of-sample robustness. They are buildable as *labeled, unvalidated candidate generators* but **not as trustworthy recommenders**, and B.2 is additionally **outside the product's stated exit-only charter**.
- **Autonomous advice of any kind via chat** is empirically risk-increasing (bias amplification) and hallucination-prone — the literature says LLM advice needs substantial human oversight to be net-positive.

The recurring theme matches the rest of the project audit: **the engineering is buildable; the data cannot make the outputs trustworthy where it matters most** — at regime shifts and in the tails. Anything that suggests *actions* (add/swap/change-logic) hits that wall; anything that *explains existing caveated numbers* does not.

## Open Questions (logged, non-blocking)

- **OQ-A1:** `migrations/003_llm_suggestions.sql` exists but is not populated by any of the three current producers — is this a vestige of a prior LLM-suggestion concept, or intended substrate for a future Narrator? (Adjacent; not audited this pass.)
- **OQ-A2:** The Divergence Explainer's forbidden-keys list (`:15-17`) is a documented convention, not a runtime assertion — nothing actively rejects a forbidden key, the protection is that no path computes one. Is a runtime guard wanted, or is construction-only sufficient? (Surfaced as a hardening option, not a recommendation.)
- **OQ-A3:** Spec Critic runs once per bundle with `symphony_id=None` (`autotuner.py:1700-1705`); the inline note flags that per-symphony SC observations would need the call moved inside the loop. Is per-symphony SC attribution wanted in the UI? (Design question for ux-designer/synthesizer.)

## Sources
- [Ledoit & Wolf — Honey, I Shrunk the Sample Covariance Matrix (SSRN 433840)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=433840)
- [Estimation of the Covariance Matrix of Large Dimensional Data (arXiv 1201.4672)](https://arxiv.org/pdf/1201.4672)
- [Bailey & López de Prado — The Deflated Sharpe Ratio (SSRN 2460551)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [Bailey & Borwein — The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- [Page & Panariello — When Diversification Fails (FAJ 2018)](https://www.tandfonline.com/doi/full/10.2469/faj.v74.n3.3)
- [Regime-switching & local Gaussian correlation (arXiv 2306.15438)](https://arxiv.org/pdf/2306.15438)
- [Biased echoes: LLMs reinforce investment biases (PLOS One / PMC12204588)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12204588/)
- [LLM Hallucinations — Implications for Financial Institutions (BizTech 2025)](https://biztechmagazine.com/article/2025/08/llm-hallucinations-what-are-implications-financial-institutions)
- [Hallucination Detection and Mitigation in LLMs (arXiv 2601.09929)](https://arxiv.org/html/2601.09929v1)
- [LLMs and Stock Investing: Is the Human Factor Required? (arXiv 2603.19944)](https://arxiv.org/pdf/2603.19944)
