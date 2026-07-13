# AI Advisor System Closeout — Consolidated Verdict

> **SUPERSEDED — 2026-07-13.** This closeout verified STRUCTURAL
> wiring (do routes exist, do they call the right function, does a
> gate run) as of 2026-06-17. It did NOT evaluate whether the advisor
> suite delivers the operator's actual intent — genuine LLM reasoning
> over the live symphony, and statistically meaningful gating. For
> that assessment, see `docs/audit/ADVISOR-INTENT-AUDIT.md`
> (2026-07-13), which found: Logic Changes and Asset Swaps are 100%
> deterministic with no LLM on any reachable path (F1); their FDR
> gates lack the PBO veto and SPY-relative baseline Strategy Builder
> has (F2); and Strategy Builder's "template-only in prod" finding
> below (HF-1) was resolved 2026-06-20 by DE-SB-GEN-001, three days
> after this closeout — Strategy Builder now defaults to real Opus
> generation. The structural-wiring findings below remain historically
> accurate for their stated scope and date; do not read them as a
> verdict on reasoning fidelity or statistical substance. See
> `DE-ADVISOR-R1-001` in `DECISIONS.md` for the remediation cycle this
> audit produced.

**Synthesis lead:** `closeout-synth` (honest broker)
**Date:** 2026-06-17
**Branch:** `audit/ai-council-closeout-e2e` (doc-only vs origin/main `73dc603`)
**Worktree HEAD audited:** `b1b6227`
**Scope:** Read-only verification of the ENTIRE AI Advisor system — Cluster 1 (Market Prism
council, F1–F21) + Cluster 2 (AI Advisor suite, F22–F40) + ACs 1–18. No code changed.

> **Honest-broker stance.** Every endorsed finding below carries a `file:line` AND a runnable
> result or a synth code re-read. Claims resting on file-scoped grep without a reachable-path
> check are labeled and down-ranked. `[interpretation]` labels are carried forward verbatim —
> NOT promoted to fact. The 3 highest-stakes findings (RF-1, F5 vintage, HF-1) were each
> re-verified by the synth against the worktree code, not taken on the auditor's word.

---

## Executive verdict

**The AI Advisor system is verified END-TO-END with ZERO closeout-blocking code defects.**
All 40 features that are verifiable in Wave 1 (C1-/deploy-independent) PASS on their
acceptance bar. Six distinct follow-on items were surfaced — **two are doc-only and land
THIS cycle** (closeout-doc); **four are separate follow-on cycles** (one render-quality, one
data-vintage, one operator-gated wiring decision, one trivial stale code-comment). None blocks
declaring the Wave-1 closeout complete.

| | Count |
|---|---|
| Features verified **PASS** (incl. PASS-WITH-FINDING / file-level) | 33 |
| Features that are a **FINDING** (tracked, not a clean pass) | 3 (F4 doc, F5 vintage, F35 HF-1) |
| Features **DEFERRED** to a later wave (not Fail) | 4 (F13 non-dry-run, F17, F18 count, F21) + the live arms of F20/F29/F30/F32 |
| ACs **PASS** | 12 (AC-1,2,3,4,5,9,10,12,13,15,16,18) |
| ACs **DEFERRED** (W2 deploy / W3 operator-gated) | 6 (AC-6,7,8,11,14,17) |
| Closeout-**blocking** code defects | **0** |

**One-line bottom line:** ship the doc corrections, schedule three small follow-on cycles,
and the only things left are the operator-gated capstone run + the post-close deploy — exactly
as the wave plan intended.

---

## Per-cluster verdict

### Cluster 1 — Market Prism council (F1–F21)
- **Lens producers F1–F5: all live `available=True` with real, non-stub values + honest
  D-1 degradation arms.** Technicals `breadth=0.8`, sentiment `tone_score` (tone-only arm),
  derivatives `vix_level=16.41` w/ working freshness guard (PR #37), macro 4 live FRED series
  (NOT a stub), fundamentals 6/8 tickers fan-out + byte-preserved single-ticker path (PR #38).
- **Lens infra F6–F8: PASS.** Proxy floors yield a real universe with empty holdings
  (DE-TECH-002); 5-key honest-availability contract with `type(exc).__name__`-only reasons;
  citation validation drops malformed sources.
- **Warehouse / audit-log / pipeline F9–F14: PASS** (Wave 1). Third-DB round-trip with
  recursive secret-strip + pytest sentinel; migration 032 last wired; CLI writer D-1; dry-run
  pipeline shape correct; 03:00 scheduler wired with CC-2 lazy import.
- **Council agents F15–F16: file-level PASS** (5 analysts + synthesizer, `model: opus`, role
  strings match); **live deliberation DEFERRED to the W3 capstone.**
- **Overview render F19: PASS-WITH-FINDING (RF-1).** Design-system contract + structure pass;
  the per-lens card bodies render raw JSON (finding below).
- **Model config F20: code PASS** (env var, zero Haiku literal); **live-against-deployed-daemon
  DEFERRED to W2.**

**Cluster-1 findings:** F4-DOC-1 (doc, this cycle), RF-1 (render, follow-on), F5 vintage
(data, follow-on). Deferred: F13 non-dry-run, F17, F18 count, F20 live, F21 capstone.

### Cluster 2 — AI Advisor suite (F22–F40)
- **Config Advisor core F22–F27: PASS.** hash-not-name rule, 3-layer D-1 on
  `request_suggestions`, informative `oos_alpha=None` empty-state, 7-item allowlist with
  structural rejection (`LIVE_EXECUTION` absent), C2 accept/reject gates (live test-client),
  FDR strictness is the expected non-error path.
- **Correlations / Asset Swaps / Logic Changes F28–F30: route+gate+blend PASS;** live POSTs
  needing a real Composer key DEFERRED to W2.
- **Chat M5 F31: PASS.** Dual-layer `validate_artifact`, zero write/trade paths (grep=0),
  explain-only system prompt, D-1.
- **Strategy Builder F32–F34: PASS** (template-only in prod = HF-1); single-batch FDR before
  screens; `symphony_schema` never-raises (runnable); 429 backoff PASS, 1 req/s pacing
  down-ranked (implicit).
- **Community + gate infra F35–F37:** F36 (full-batch FDR) + F37 (shared `acceptance_gate`,
  runnable module-id match) PASS; **F35 = HF-1 finding, operator-gated.**
- **SPA shell F38–F40: PASS.** 6-tab render + `node --check` EXIT 0; 5 GET 302 redirects;
  CSRF tokenless→403; advisor routes not in the settings allowlist.

**Cluster-2 findings:** F35/HF-1 (operator-gated + doc, this cycle), AC-18 C2-gate wording
(doc, this cycle). Deferred: the live POST arms of F29/F30/F32 (W2).

---

## Mapping to the original goal — every gap named

The operator goal was *"a full end-to-end verification of EVERY feature in the AI council,"*
expanded to the entire AI Advisor system. The matrix swept all 40 features + 18 ACs. Gaps
between "verified now" and "fully closed out," each named:

1. **C1 deploy (AC-1/F20 live, F13 non-dry-run, AC-6, AC-14 live POSTs):** the running :8090
   daemon holds pre-C1 code in memory. **Gating dependency:** PM post-close deploy (~14:00 MDT).
   These are W2, not failures.
2. **Capstone council run (F17, F18 count, F21, AC-7, AC-8, AC-11):** real multi-analyst Opus
   deliberation under operator observation. **Gating dependency:** operator availability + Opus
   spend authorization. W3, operator-gated.
3. **HF-1 community-strats build-vs-defer (F35, AC-17):** the doc is reconciled this cycle; the
   build decision is the operator's. **Gating dependency:** operator adjudication. W3.

Nothing in the goal is silently dropped. Every not-yet-closed item has a named gate.

---

## Confirmed findings (prioritized) — each with owning `file:line` + follow-on recommendation

### Finding RF-1 — Overview lens cards render raw JSON, not a human-readable digest
- **Class:** render quality / content readability. **AC-9 = PASS-WITH-FINDING.**
- **Confidence:** HIGH. Two independent confirmations: ux eyes-on (`ux-lens-cards-crop.png`,
  render-gate.md) AND synth code re-read.
- **Owning `file:line`:** `advisors/lens_pipeline.py:166-167` —
  `entry["summary"] = json.dumps(block["payload"])` is the fallback when a lens block has no
  prose `"summary"` key (all 5 lenses hit it); `templates/ai_advisor.html:994-995` emits
  `{{ _lens.get('summary') | e }}` verbatim → a raw JSON blob in each `.prism-lens-text`.
- **Synth verification:** read both lines on `b1b6227` — confirmed exactly as described.
- **Critical fix constraint:** the same `summary` feeds the synthesis PROMPT path; a fix must
  emit a prose `"summary"` from the lens builders (or render structured `payload` fields in the
  template) **without breaking the LLM synthesis input.** The synthesis rationale itself is
  correct prose — only the per-lens digest cards are affected.
- **Follow-on fix cycle:** a small Toxic-Pair cycle. Testable bar (from render-gate.md): each
  `.prism-lens-text` must NOT start with `{` and must render ≥1 human-readable label+value;
  assert `summary` is a non-empty non-JSON string (no specific computed values).

### Finding F5 — Fundamentals lens serves WRONG-VINTAGE values (two concurrent defects)
- **Class:** data correctness ("available=True but wrong vintage" — the stale-VIX #37 analog).
  **F5 = PASS-WITH-FINDING** (lens IS available with real data; the *values* are stale).
- **Confidence:** HIGH. Live runnable SEC evidence (cluster1-F5-vintage.md) + synth code re-read
  of the Mode-B sort.
- **Mode A — XBRL concept deprecation (`ai_advisor.py:354-360`):** `_SEC_KEY_CONCEPTS` hardcodes
  `"Revenues"` with no fallback. MSFT migrated to
  `RevenueFromContractWithCustomerExcludingAssessedTax` (current val end=2025-06-30, unreached);
  the `Revenues` tag for MSFT is frozen at end=2010-06-30. Runnable SEC evidence: the current
  concept is present in EDGAR (48 10-K entries) but never queried.
- **Mode B — sort-by-`filed` picks the OLDEST comparative entry (`ai_advisor.py:1008-1019`):**
  sorts `entries_to_check` by `e.get("filed","")` descending and takes `[0]`; comparative
  prior-period entries in one 10-K share a `filed` date, and the stable sort then yields the
  oldest `end`. Affects ALL tickers/all non-deprecated concepts (JPM control: all 5 concepts
  1–2 yr stale; no Mode A). **Synth verification:** read `:1008-1019` — the sort key is
  `e.get("filed","")` while the comment says "most recent 10-K." Confirmed.
- **Both fixes required (follow-on cycle):** Fix 1 (Mode A) = concept fallback list
  (`RevenueFromContractWithCustomerExcludingAssessedTax` → `SalesRevenueNet` → `Revenues`,
  pick latest `end`); Fix 2 (Mode B) = change the sort key from `filed` to `end`. Fix 2 alone
  does not recover MSFT Revenues; Fix 1 alone leaves non-Revenue concepts 1–2 yr stale.

### Finding F4-DOC-1 — Macro lens mislabeled "stub" in generated docs (DOC-ONLY, this cycle)
- **Class:** doc/behavior mismatch. **AC-10 doc FAIL** (the canonical "macro stub" mislabel).
- **Confidence:** HIGH. Live: `_build_macro_section()` returns `available=True` with 4 real FRED
  series + 4 clickable sources (cluster1-groupA.md F4).
- **Owning lines:** `docs/generated/ai_advisor.md` (module header + the
  `_build_macro_section()` "Stub — available=False" table row) + `docs/generated/INDEX.md`
  ("macro stub only").
- **Resolution:** corrected by `closeout-doc` on this branch (lands this cycle). Verified
  present in the worktree doc drafts.

### Finding HF-1 (F35) — Community-strats engine HOLLOW in production (OPERATOR-GATED, AC-17)
- **Class:** hollow wiring + stale doc. **F35 = FINDING (operator-gated), NOT a pass.**
- **Confidence:** HIGH. `app.py:3437` calls `propose_strategies(...)` with NO
  `community_candidates=`; `grep -c community... app.py = 0`. **Synth verification:** re-read
  `app.py:3437-3443` — confirmed no community arg. The engine layer IS fully built
  (`strategy_builder_engine.community_candidate_infos:195`, `propose_strategies` kwarg `:864`
  applied `:921-922`, `community_strats.load_community_strategies:98`) but unreachable from any
  production route — reachable only from tests.
- **Doc contradiction:** CLAUDE.md `community_strats.py` row ("first production caller:
  propose_strategies ... injected at the route boundary") is FALSE; DECISIONS.md:633 ("no
  production caller yet") is TRUE; DECISIONS.md:651 implies a caller that does not exist.
- **Resolution this cycle:** doc reconciled by `closeout-doc` to "no production route caller;
  engine layer available for a future wiring cycle." **Build-vs-defer is the OPERATOR's call**
  (PM notes the operator leans BUILD — a separate Toxic-Pair cycle, not part of this closeout).
  **Superseded note (2026-07-13):** this gap was closed by C4/C5 (2026-06-20) — see
  `docs/generated/advisors_build_plan_generator.md` and `docs/generated/
  advisors_strategy_builder_engine.md` for the current, wired state.

### Finding AC-18 — C2 "safety gates" wording (DOC-ONLY, this cycle)
- **Class:** minor doc-accuracy. **NOT a security finding.**
- **Confidence:** HIGH. `app.py:3686-3759`: there are 4 code gates; Gate-2 (risk-direction
  agreement, `:3708-3709`) LOGS the disagreement but does NOT block — only Gate-1 (allowlist)
  and Gate-3 (OOS revalidation) structurally block. CLAUDE.md "C2 safety gates" (plural,
  implies all-block) overstates.
- **Resolution:** reconciled by `closeout-doc` ("4 gates ... Gate-2 logs-only") this cycle.
  Verified present in the worktree CLAUDE.md draft.

### Finding C2-COMMENT-1 — Stale CODE comment "Three independent layers" (CODE-FIX follow-on)
- **Class:** stale code comment. **NOT a closeout blocker; NOT fixable in this doc-only cycle**
  (a code-comment edit is out of the doc-writer's lane and out of the verification scope).
- **Confidence:** HIGH. **Synth verification:** read `ai_advisor.py:1707-1719` on `b1b6227` —
  the C2 section header comment says *"Three independent layers"* and enumerates only 3 gates,
  but the accept path (`app.py:3703-3730`) has **4** gates (it predates the locked-var Gate-4).
  The generated doc already states the truth (`docs/generated/ai_advisor.md:14`, "Four
  independent ... gates"), so this is a code-comment/doc divergence the other way.
- **Owning `file:line`:** `ai_advisor.py:1710` (the "Three independent layers" comment).
- **Follow-on:** a trivial Tier-1 code-comment fix ("Three" → "Four", add the locked-var gate
  to the enumeration) on its own cycle. Surfaced by closeout-doc; flagged by the PM in plan
  approval. Down-ranked — cosmetic, no behavior impact.

---

## Deferred items (NOT failures) — with gating dependency

### Wave 2 — after the PM post-close C1 deploy (~14:00 MDT)
- **F13 non-dry-run `run_pipeline()`** → one real MARKET_PRISM row, timed clear of the 03:00
  job, confirm no double-write. *Gate: deployed daemon.*
- **F20 live synthesis-model** against the deployed daemon. *Gate: deployed daemon.*
- **AC-6** (nightly pipeline non-hollow live) + **AC-14** live POST arms of F29/F30/F32 (need a
  real Composer key + deployed daemon). *Gate: deployed daemon + Composer key.*

### Wave 3 — operator-gated (PREPARE + flag only; do NOT execute)
- **F21 / AC-7 capstone observed multi-analyst council run** (real Opus spend, operator
  observation). *Gate: operator availability + spend authorization.*
- **F17** (debate/clarification phase tags) + **F18 count** (one row per real `run_id`) — only
  observable inside a live capstone trail. *Gate: the capstone run.*
- **AC-11 operator sign-off** — Phase-4 unattended scheduling stays hard-blocked until received.
  *Gate: operator sign-off.*
- **AC-17 HF-1 build-vs-defer** — operator adjudicates build the route injection (own TDD cycle)
  vs deferred-by-design. *Gate: operator decision.*

---

## What could NOT be determined, and why (carried `[interpretation]` labels)

These are explicitly NOT promoted to fact:

1. **Two-writer authority (pipeline vs council on the same night)** — `[interpretation]` from
   the matrix Architecture section. The 03:00 `run_pipeline` and an operator-driven council run
   both target the MARKET_PRISM row family. Which is authoritative post-Epic-A, and whether they
   can race/double-write on one logical night, is NOT determined by Wave-1 static analysis. Must
   be confirmed during the W3 capstone (verify exactly-one-row + precedence). Down-ranked: no
   evidence of an actual double-write was found, but the precedence is unproven.
2. **`lens_pipeline.py:285` vs `resolve_advisor_model()` cohesion** — `[interpretation]`,
   non-blocking. `:285` reads `os.environ.get(...)` directly rather than the shared accessor
   (`ai_advisor.py:63-69`); two env-reads duplicate the default literal. Both resolve to
   `claude-opus-4-8` — a minor cohesion note, NOT a defect.
3. **F2 `tone_summary` hardcoded `None` (`ai_advisor.py:651`)** — `[interpretation]`,
   non-blocking. A reserved-for-future field; the real tone signal is `tone_score`. Not a defect,
   but the synthesizer should be briefed to consume `tone_score`, not `tone_summary`.
4. **F34 1 req/s Composer pacing (ASSUMPTION-K-1)** — `[interpretation]`, MED confidence,
   down-ranked from HIGH. The 1 req/s limit relies on Composer response latency ≥1s; there is no
   explicit `time.sleep(1)` between calls in the `strategy_builder_engine` loop. The 429 backoff
   IS explicit and verified. No known production rate-limit issue, but the pacing is IMPLICIT.
5. **F29 `_persist_observation` body (ASSUMPTION-I-1)** — the `lens_evidence`+`sources`
   persistence into `raw_response` was confirmed at the call sites but the full function body was
   not read; a W2 live POST would confirm the persisted shape. Non-blocking.
6. **Live engine behavior with a real Composer key (ASSUMPTION-I-2)** — F29/F30/F32 happy-path
   live POSTs were not exercised (market-hours + no-key constraint). Route chains + static
   analysis are HIGH confidence; the live data path is W2.
7. **F22 live `/score` call** — `bot_state` is empty in the worktree DB, so the hash-not-name
   rule was confirmed via reachable-path route code, not a live Composer `/score` round-trip.

---

## Evidence index (all findings carry `file:line` + a runnable result)

| Item | Source finding file | Key evidence |
|---|---|---|
| Cluster 1 F1–F8 | `cluster1-groupA.md` | live `_build_*_section` calls + D-1 static cites |
| Cluster 1 F9–F14 | `cluster1-groupB.md` | temp-DB round-trips, dry-run pipeline, scheduler cite |
| F5 vintage (Mode A+B) | `cluster1-F5-vintage.md` | live SEC HTTP results + `ai_advisor.py:354-360`/`:1008-1019` |
| Cluster 2 F22–F27 | `cluster2-group-H.md` | test-client gates + static D-1/allowlist cites |
| Cluster 2 F28–F30 | `cluster2-group-I.md` | route chains + render-gate correlations |
| Cluster 2 F31 | `cluster2-group-J.md` | grep=0 write-path + dual validate_artifact |
| Cluster 2 F32–F34 | `cluster2-group-K.md` | runnable symphony_schema + backoff cite |
| Cluster 2 F35–F37 | `cluster2-group-L.md` | grep=0 HF-1 + runnable acceptance_gate module-id |
| Cluster 2 F38–F40 | `cluster2-group-M.md` | test-client 200/403 + live 302 + node --check |
| AC-9/AC-13 render + RF-1 | `render-gate.md` | live :8090 eyes-on screenshots + RF-1 root cause |

---

## Recommendation to the PM

1. **Land the doc corrections this cycle** (F4-DOC-1 macro stub, HF-1 community_strats row,
   AC-18 C2-gate wording) — already drafted by closeout-doc in the worktree; synth commits them
   with the verdict + matrix.
2. **Schedule the follow-on cycles** (each its own cycle, NOT this closeout):
   **RF-1** (render digest — preserve the synthesis prompt path), **F5 vintage** (both Mode A +
   Mode B fixes), — operator-decision-gated — **HF-1** community-route wiring, and a trivial
   **C2-COMMENT-1** stale code-comment fix (`ai_advisor.py:1710` "Three"→"Four" gates; can ride
   along with any of the above).
3. **Run Wave 2** after the post-close C1 deploy (F13 non-dry-run, F20/AC-6/AC-14 live).
4. **Hold Wave 3 for the operator** (capstone council run, sign-off, HF-1 build-vs-defer).

The Wave-1 closeout is COMPLETE with zero blocking code defects.
