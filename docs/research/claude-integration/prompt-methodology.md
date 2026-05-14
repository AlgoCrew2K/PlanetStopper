# Prompt Design + Methodology for Sound LLM Config Suggestions

**Status:** Research findings — no feature code. Informs design of the on-demand "ask Claude
to suggest config edits" feature for AlphaBot v3.
**Author:** quant-risk-researcher
**Date:** 2026-05-14
**Scope boundary:** This document surfaces options + trade-offs + failure modes. It does NOT
recommend an implementation path or make design decisions — those belong to the PM + operator.

---

## 0. Context grounding (from the codebase)

The feature will send Claude:

- **Quant/math data** — outputs of `math_engine.py`: 20-day vol, 14-day ATR%, MC "prob beating
  benchmark", VWAP deviation signals, current trailing-stop distances.
- **Optuna walk-forward config** — `autotuner.py` produces, per symphony, a `best_params` dict
  over 7 tunable keys (`OPTUNA_SEARCH_SPACE_KEYS`), plus train alpha, OOS alpha, fallback OOS
  alpha, default OOS alpha, and a `_baseline_chosen` decision string ("Adopted AI" / "Reverted
  to Fallback" / "Reset to Global Default").
- **Per-symphony info** — `symphony_strategies` table: `parameters`, `locked_vars`.

The 7 tunable params and their **Optuna search ranges** (the authoritative valid ranges — these
are the ground truth the prompt must carry):

| Param | Optuna range (`autotuner.py:302-308`) | What it does (`math_engine.py` / `autotuner.py`) |
|---|---|---|
| `TRIGGER_THRESHOLD_PCT` | 5.0 – 25.0 | MC-prob upper bound for arming the risk guard; ×2 is the disarm threshold. **In `DEFAULT_LOCKED_VARS` — locked by default.** |
| `TAKE_PROFIT_MC_PCT` | 2.0 – 10.0 | MC-prob lower bound for arming; also TP-exit arming threshold |
| `VWAP_CROSS_HWM_PCT` | 0.5 – 2.5 | HWM gate for VWAP System-A profit-protection break |
| `VWAP_BLEED_MULTIPLIER` | 0.5 – 3.0 | Scales `symphony_vol` → VWAP-bleed arm threshold (clamped −3.0…−0.5) |
| `VWAP_BLEED_TICKS` | 3 – 30 (int) | Consecutive bleed ticks to confirm a bleed cut |
| `PARABOLIC_VELOCITY_THRESHOLD` | 1.0 – 4.0 | Return-velocity to arm the parabolic squeeze |
| `MAX_PARABOLIC_SQUEEZE` | 0.1 – 0.8 | Multiplier applied to the active stop once para-armed or breakeven-locked (**< 1.0 = tightens**) |

**Critical observation for failure-mode analysis:** the autotuner already has a robust
selection cascade — it only adopts AI/Optuna params if `oos_alpha > fallback AND > default`
(strict-positive tie rule, `autotuner.py:390`). Any Claude-suggested edit *bypasses* that
OOS gate unless the design re-runs validation. This is the single most important framing for
the whole feature: **Claude's suggestion is an unvalidated hypothesis; Optuna's is a
walk-forward-validated one.** The prompt and the workflow must never let those be confused.

---

## 1. Prompt content for soundness

### 1.1 Must-have elements (without these, output is a confident guess)

1. **Per-param definition + directionality + risk polarity.** For each param: one sentence on
   what it controls, and explicitly whether increasing it *loosens* or *tightens* risk. Claude
   cannot reason about "is this safe" without knowing that `MAX_PARABOLIC_SQUEEZE` below 1.0
   tightens, or that raising `VWAP_BLEED_TICKS` *delays* an exit (loosens protection).
2. **Valid range of every param** — the Optuna `suggest_*` bounds above, stated as hard
   min/max. This is the single highest-leverage anti-hallucination element: it converts an
   open-ended generation into a bounded one.
3. **The Optuna OOS-vs-train delta.** Send `train_alpha`, `oos_alpha`, and the gap. A large
   positive train alpha with a collapsed or negative OOS alpha is the overfitting signal —
   Claude can only flag "Optuna may have overfit here" if it can see both numbers. Also send
   `fallback_oos_alpha`, `default_oos_alpha`, and `_baseline_chosen` so Claude knows whether
   the live config is even Optuna's pick or a reverted baseline.
4. **Current live value of each param** (the `symphony_strategies.parameters` row) — the
   anchor every suggestion is a diff against.
5. **`locked_vars`.** Locked params (e.g. `TRIGGER_THRESHOLD_PCT` by default) must be marked
   no-edit in the prompt. Claude should be told it may *comment* on a locked param but must
   not emit a suggested new value for it.
6. **Volatility regime context.** Current 20-day vol and 14-day ATR% vs. their range over the
   125-day window. "Are we in a high-vol regime the tuning window under-samples?" is
   unanswerable without this.
7. **The data window + its limits.** State explicitly: tuning is 125 trading days, 80/20
   walk-forward; synthetic/replay history; what market regimes are *and are not* in that
   window. This is what lets Claude invoke the "insufficient data" escape hatch (§2.3).
8. **Risk invariants as hard constraints.** Enumerate the non-negotiables: the trailing-stop
   ratchet (monotonic, never moves down — `compute_breakeven_update`), the breakeven latch is
   one-way, exit-confirmation tick gates exist for a reason. Any suggestion that would
   functionally loosen a stop must be called out by Claude itself as risk-increasing.
9. **The task framing + role.** "You are assisting a human operator who reviews every
   suggestion. You are NOT tuning the system; you propose hypotheses for a human + a
   walk-forward validator to test. Prefer fewer, well-justified suggestions over many."

### 1.2 Ideal prompt structure (a layout option, not a mandate)

```
[SYSTEM]
  Role: operator-assist analyst. Human reviews/accepts/rejects every suggestion.
  Hard rules: stay within stated valid ranges; never edit locked params; every
  suggestion needs a rationale tied to supplied data; if data is insufficient,
  say so and decline. Output ONLY the specified JSON schema.

[CONTEXT — GROUND TRUTH]
  Symphony: <name>
  Data window: 125 trading days, 80/20 WFA, synthetic replay history.
  Regimes present: <...>.  Regimes NOT covered: <...>.
  Current volatility: 20d vol = X (window range A–B); 14d ATR% = Y (range C–D).

[PARAM TABLE]
  For each of the 7 params: name | current live value | valid range |
  locked? | direction (raise = loosen/tighten risk) | one-line purpose.

[OPTUNA EVIDENCE]
  Per symphony: train_alpha, oos_alpha, oos–train gap, fallback_oos_alpha,
  default_oos_alpha, _baseline_chosen.

[RISK INVARIANTS]
  Trailing-stop ratchet (monotonic up). Breakeven latch one-way. Exit-confirm
  tick gates. "A suggestion that loosens a live stop must be self-flagged."

[TASK]
  Propose 0..N config edits. Each: param, current, suggested, rationale citing
  specific supplied numbers, risk_direction, confidence, data_sufficiency.
  If no edit is well-supported, return an empty suggestions list with an
  explanation. Do NOT fabricate to fill the list.

[OUTPUT SCHEMA]
  Strict JSON. (see §2)
```

Reasoning support: ask Claude to produce a brief `analysis` field *before* the
`suggestions` array (chain-of-thought-then-structured), so the rationale is reasoned, not
post-hoc rationalized. `[Interpretation]` — this ordering is a known prompt-engineering
lever for grounded output, but its benefit here is unmeasured for this specific task.

---

## 2. Constraining the output

Split guardrails into **prompt-side** (instructions Claude is asked to honor) and
**code-side** (deterministic validation of Claude's response that does NOT trust the model).
The governing principle from the high-stakes-LLM literature: prompt-side guardrails reduce
the rate of bad output; only code-side validation *guarantees* a bad output is caught. For a
real-money system, treat every prompt-side guardrail as advisory and every invariant as
code-enforced.

### 2.1 Prompt-side guardrails

- Required `rationale` per suggestion, must cite specific supplied numbers (not "market
  conditions suggest…").
- Required `data_sufficiency` field per suggestion: `sufficient` / `thin` / `insufficient`.
- Required `risk_direction` field: `tightens` / `loosens` / `neutral` — forces Claude to
  classify its own suggestion's risk polarity.
- Required `confidence` field: `low` / `medium` / `high` with a one-line reason.
- Instruction: stay within valid ranges; suggested value must be inside the stated min/max.
- Instruction: never emit a suggested value for a locked param.
- Instruction: empty suggestions list is a valid, encouraged answer when nothing is
  well-supported.

### 2.2 Code-side validation (the actual guarantee — does not trust the model)

- **Schema validation** — reject any response that is not the exact JSON schema. Malformed →
  surface "Claude returned an invalid response," suggest nothing.
- **Range clamp/reject** — any `suggested` value outside the Optuna `suggest_*` bounds is
  *rejected outright* (not clamped — a clamped value is no longer Claude's reasoned output).
  This mirrors the autotuner's own `OPTUNA_SEARCH_SPACE_KEYS` "no Frankenstein merge" posture.
- **Locked-param filter** — drop any suggestion targeting a param in `locked_vars`, regardless
  of what the prompt said.
- **Type/int enforcement** — `VWAP_BLEED_TICKS` is an int; reject non-int suggestions.
- **Rationale non-empty + min-length** — reject empty or template rationales.
- **Risk-direction sanity check** — code can independently compute whether a suggested delta
  loosens risk (e.g. `MAX_PARABOLIC_SQUEEZE` up, `VWAP_BLEED_TICKS` up, `TAKE_PROFIT_MC_PCT`
  movement). If Claude labeled it `tightens` but code says `loosens`, flag the contradiction
  prominently to the operator — a self-misclassification is a strong distrust signal.
- **Magnitude cap (option)** — consider rejecting/flagging suggestions that move a param more
  than X% of its range in one step; large jumps are where anchoring-failures and overfit
  hypotheses concentrate. `[Interpretation]` — threshold choice is an operator decision.
- **Idempotent escape hatch** — an empty suggestions list must flow through cleanly as
  "Claude had no well-supported suggestion," not as an error.

### 2.3 The "insufficient data to suggest" escape hatch

This is the most important single guardrail and it must exist in **both** layers:

- **Prompt-side:** explicitly tell Claude that declining is a correct, valued answer; give it
  the `data_sufficiency: insufficient` value and an empty-list option; tell it *why* it might
  decline (regime not in window, OOS alpha too noisy, conflicting signals).
- **Code-side:** the workflow must render an empty list and per-suggestion `insufficient`
  flags as first-class UI states, not failures. If the only thing standing between "decline"
  and "fabricate" is the prompt, the model will sometimes fabricate. The R-Tuning /
  abstention literature (arXiv 2510.24476; MDPI 14/8/332) is consistent: models abstain
  reliably only when abstention is an explicitly modeled, low-friction output — never when
  it is merely "allowed."

---

## 3. Failure modes + mitigations

Each row: failure → why it happens → prompt-side mitigation → code-side mitigation.

### 3.1 LLM over-fitting to recent data
- **Why:** the quant snapshot is "now"; Claude weights the most salient recent numbers and
  proposes a config that fits the last few days.
- **Prompt:** send the *full* 125-day window stats (ranges, not just current values); state
  explicitly "do not optimize for the current snapshot — these params persist across
  regimes"; send the OOS-vs-train gap as the cautionary example of what overfitting looks
  like.
- **Code:** any adopted suggestion should be routed back through the autotuner's OOS
  simulation before going live (re-use `run_simulation` on `history_test`) — never let a
  Claude edit reach live config without the same walk-forward gate Optuna's own output faces.

### 3.2 Suggestion violates a risk invariant (e.g. loosening a trailing stop)
- **Why:** a loosened stop often *improves* a naive backtest metric (fewer early exits → less
  "missed upside" penalty in `run_simulation`). A plausible-sounding rationale follows easily.
- **Prompt:** enumerate the invariants; require `risk_direction`; instruct Claude to
  self-flag any loosening suggestion as risk-increasing and justify it extra-carefully.
- **Code:** independent risk-direction computation (§2.2); surface every `loosens` suggestion
  with a distinct visual treatment in the operator diff; never auto-accept; consider a hard
  block on suggestions that loosen during a flagged high-vol regime.

### 3.3 Anchoring on Optuna's exact numbers
- **Why:** Optuna's `best_params` are in the prompt; Claude treats them as authoritative and
  either parrots them or makes trivial ±epsilon nudges that add no information.
- **Prompt:** frame Optuna's numbers as "one validated hypothesis, not ground truth"; send
  the `_baseline_chosen` field so Claude sees when Optuna's pick was *rejected* by the
  cascade; ask Claude to reason from the *data*, and to explicitly say when it agrees with
  Optuna and why rather than restating it.
- **Code:** flag suggestions that are within epsilon of either the current value or the
  Optuna value as "low information" so the operator isn't asked to review noise.

### 3.4 Recency bias
- **Why:** related to 3.1 — recent regime dominates the rationale.
- **Prompt:** require each rationale to cite at least one *window-level* statistic (a range,
  an OOS figure), not only a current-snapshot value.
- **Code:** rationale-content check — reject/flag rationales that reference only current
  values and no window/OOS context.

### 3.5 Suggesting changes during a regime the data doesn't cover
- **Why:** the 125-day synthetic window may not contain the current regime (vol spike, gap
  event, low-volume session). Claude, not told this, suggests anyway.
- **Prompt:** explicitly list regimes *not* covered by the window; instruct Claude that if
  the current vol/ATR is outside the window's observed range, the correct answer is
  `data_sufficiency: insufficient` + decline.
- **Code:** compute "is current vol/ATR outside the 125-day observed range?" deterministically
  *before* the call; if so, inject a hard flag into the prompt and independently down-rank or
  block any non-`insufficient` suggestion. Do not rely on Claude to notice.

### 3.6 (Additional, adjacent — logged, not deep-dived) Sycophancy / confirmation
- Claude may infer the operator *wants* a change and produce one to be helpful. Mitigation:
  prompt framing that an empty list is a successful outcome; never phrase the request as
  "what should we change?" — phrase as "is there anything the data well-supports changing?"

**Top 3 by blast radius:** (1) risk-invariant violation / stop-loosening — directly endangers
real money; (2) regime-not-covered suggestions — confident output on out-of-distribution
input; (3) over-fitting to recent data — silently degrades the config the autotuner worked to
validate. All three share one mitigation backbone: **route any accepted suggestion through
the existing autotuner OOS gate before it reaches live config**, and compute regime/risk-
direction facts in code rather than trusting the model to self-report them.

---

## 4. Audit + compliance flags

**Posture statement (suggested wording for the operator-facing doc / DECISIONS.md):**

> This feature is an **operator-assist tool with a mandatory human-in-the-loop**. Claude
> produces *suggestions only*. No Claude output reaches live trading config without an
> explicit human accept action, and (recommended) without passing the autotuner's existing
> out-of-sample validation gate. Claude is not an "AI agent that transacts"; it does not act,
> it advises. The human operator remains the decision-maker and the accountable party for
> every config change.

This framing matters: 2025–2026 SEC/FINRA guidance (Sidley, Feb 2025; FINRA 2026 Annual
Regulatory Oversight Report) draws a sharp line between AI that *advises* and AI agents that
*act/transact* — the latter attract "narrow scope, permissions, audit trails of actions,
explicit human checkpoints before execution." Keeping this feature unambiguously on the
*advice* side of that line is the lightest-weight compliance posture available, and the
posture statement should say so explicitly.

### 4.1 What the operator must be explicitly warned about (in-UI)

- Claude's suggestions are **unvalidated hypotheses**, not walk-forward-validated like
  Optuna's output. The UI should say this near the suggestions.
- LLMs can produce confident, plausible-sounding rationales for wrong suggestions
  ("hallucination"). A coherent rationale is *not* evidence the suggestion is sound.
- The model only sees what the prompt sends — it has no knowledge of live market conditions,
  news, or anything outside the supplied snapshot + window.
- Accepting a suggestion that loosens a risk control increases downside exposure; these are
  flagged for a reason and deserve extra scrutiny.
- The operator, not Claude, owns the outcome of any accepted change.

### 4.2 Required audit trail

Persist, for **every** suggestion (accepted *and* rejected), an immutable, timestamped record:

- Timestamp (UTC), symphony name, operator identity.
- **Full prompt inputs** — the exact quant snapshot, Optuna evidence, param table, ranges,
  and regime context sent to Claude (so a decision is reconstructable later).
- **Model identity + version** and generation settings (temperature, etc.).
- **Full raw Claude response** — including `analysis`, every suggestion, rationale,
  confidence, data_sufficiency, risk_direction.
- **Code-side validation results** — what passed, what was rejected/clamped/flagged and why.
- **Operator decision per suggestion** — accept / reject — with timestamp, and (recommended)
  an optional operator note.
- **If accepted:** the before/after config values, and the result of the post-accept OOS
  re-validation if that gate is implemented.

This mirrors what `autotuner.py` already does loosely via `optimization_results` +
`post_mortem_*.json`, and what the AlphaBot two-DB pattern supports — a dedicated
`llm_suggestions` audit table in the state DB (additive, NULLable + DEFAULT per the project
migration rule) is the natural home. The literature is explicit (NYSBA 2025; FINRA 2026
Report): the audit trail must capture the *complete decision chain* — inputs, model outputs,
weighting, and every human intervention/override — not just the final decision.

### 4.3 Compliance scope note

Keep this lightweight and **flag, don't decide**: whether AlphaBot is operated personally vs.
on behalf of others materially changes the regulatory surface (FINRA membership, adviser
registration, recordkeeping rules). The records architecture above is good practice
regardless, but **binding determinations on registration, recordkeeping retention periods,
and disclosure obligations require qualified securities counsel.** This document does not
provide legal advice and should not be read as a compliance sign-off. Action item: log an
open question for the operator to confirm the operating context and, if it is anything other
than purely personal, consult counsel before launch.

---

## 5. Literature

**Plain statement: the literature specific to "LLM suggests parameters for a quantitative
trading config" is thin to non-existent.** What exists falls into three adjacent buckets:

**A. LLMs in quantitative investment (adjacent, not on-point).**
- *From Deep Learning to LLMs: A survey of AI in Quantitative Investment* (arXiv 2503.21422,
  2025) — surveys LLM use across the quant pipeline; does not cover risk-engine parameter
  suggestion. `[Tier 3 — preprint, not peer-reviewed]`
- *Language Model Guided Reinforcement Learning in Quantitative Trading* (arXiv 2508.02366,
  FLLM 2025 preprint) — LLM guiding an RL trader; notes practical generation settings
  (temperature 0.7, frequency penalty 1.0, presence penalty 0.25). `[Tier 3]`
- *Enhancing LLM Performance in Asset Selection* (ACM, 2025 Conf. on Digital Economy,
  Blockchain & AI) — relevant negative result: when OLS/XGBoost predictions were fed to the
  LLM as supplementary input, its performance *deteriorated*. `[Interpretation]` — a caution
  that more quantitative context is not monotonically better; the prompt should send
  *decision-relevant* structured data, not everything available. `[Tier 2/3 — peer-reviewed
  conference]`

**B. LLM hallucination mitigation in high-stakes settings (on-point for §2–§3).**
- *Mitigating Hallucination in LLMs: An Application-Oriented Survey on RAG, Reasoning, and
  Agentic Systems* (arXiv 2510.24476, 2025) — five mitigation classes: external knowledge
  grounding, confidence calibration, prompt engineering, decoding control, fine-tuning.
  Directly supports the §1 grounding requirements and §2 guardrail split. `[Tier 3]`
- *Multi-Layered Framework for LLM Hallucination Mitigation in High-Stakes Applications*
  (MDPI Computers 14/8/332, 2025) — three-layer architecture: input governance →
  evidence-grounded generation → post-response verification. Maps cleanly onto: prompt
  inputs (§1) → constrained generation (§2.1) → code-side validation (§2.2). `[Tier 2 —
  peer-reviewed]`
- R-Tuning / abstention work (referenced in the above surveys) — models abstain reliably
  only when abstention is explicitly modeled as a low-friction output. Directly supports
  §2.3. `[Tier 3]`

**C. AI compliance in financial services (on-point for §4).**
- Sidley Austin, *Artificial Intelligence: U.S. Securities and Commodities Guidelines for
  Responsible Use* (Feb 2025) — no AI-specific rules yet; existing frameworks apply;
  diligence expected. `[Tier 2 — law-firm analysis]`
- FINRA *2026 Annual Regulatory Oversight Report* (Dec 2025) — dedicates a section to
  GenAI as a "supervised technology"; for AI agents that act/transact: narrow scope,
  permissions, action audit trails, explicit human checkpoints. `[Tier 1 — regulator
  publication]`
- NYSBA, *Regulating AI Deception in Financial Markets* (2025) — argues for comprehensive,
  time-stamped audit trails covering the *complete decision chain* including data inputs and
  human overrides. `[Tier 2/4 — bar association article]`

**Verification status:** §1–§3 claims triangulate across the two hallucination surveys
(different venues, consistent on the grounding + abstention + verification split) →
`[Medium-High]`. §4 claims triangulate across a regulator publication + two independent legal
analyses → `[Medium-High]` on the factual posture, `[Unverified]` on any specific binding
obligation (counsel-dependent). The "LLM-for-config-tuning literature is thin" claim is
itself `[Medium]` — absence of evidence from targeted search, not proof of absence.

---

## 6. Open questions (logged, not in scope to resolve here)

1. Will accepted suggestions be routed back through the autotuner OOS gate before going live?
   This research strongly surfaces it as the highest-leverage mitigation, but it is an
   implementation decision for the PM + operator.
2. Operating context (personal vs. on-behalf-of-others) — gates the compliance surface;
   needs operator confirmation and possibly counsel.
3. Generation settings (temperature etc.) for this task — adjacent; the FLLM-2025 preprint's
   0.7 is one data point, not a validated choice for this use case.
4. Magnitude-cap threshold for single-step param moves (§2.2) — operator decision.

---

## Sources

- [From Deep Learning to LLMs: A survey of AI in Quantitative Investment (arXiv 2503.21422)](https://arxiv.org/html/2503.21422v1)
- [Language Model Guided Reinforcement Learning in Quantitative Trading (arXiv 2508.02366)](https://arxiv.org/html/2508.02366v1)
- [Enhancing LLM Performance in Asset Selection (ACM 3762249.3762294)](https://dl.acm.org/doi/full/10.1145/3762249.3762294)
- [Mitigating Hallucination in LLMs: An Application-Oriented Survey (arXiv 2510.24476)](https://arxiv.org/html/2510.24476v1)
- [Multi-Layered Framework for LLM Hallucination Mitigation in High-Stakes Applications (MDPI Computers 14/8/332)](https://www.mdpi.com/2073-431X/14/8/332)
- [A Survey on Hallucination in LLMs: Definitions, Detection, and Mitigation (Preprints.org 202510.0540)](https://www.preprints.org/manuscript/202510.0540/v1)
- [Sidley Austin — AI: U.S. Securities and Commodities Guidelines for Responsible Use (Feb 2025)](https://www.sidley.com/en/insights/newsupdates/2025/02/artificial-intelligence-us-financial-regulator-guidelines-for-responsible-use)
- [FINRA 2026 Annual Regulatory Oversight Report](https://www.finra.org/sites/default/files/2025-12/2026-annual-regulatory-oversight-report.pdf)
- [NYSBA — Regulating AI Deception in Financial Markets](https://nysba.org/regulating-ai-deception-in-financial-markets-how-the-sec-can-combat-ai-washing-through-aggressive-enforcement/)
- [Shumaker — Generative AI in Financial Services: A Practical Compliance Playbook for 2026](https://www.shumaker.com/insight/client-alert-generative-artificial-intelligence-in-financial-services-a-practical-compliance-playbook-for-2026/)
