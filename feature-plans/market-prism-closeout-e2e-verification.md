# Feature: AI Advisor System Closeout — Full End-to-End Verification
Status: ready
Created: 2026-06-17
Updated: 2026-06-17 (scope expanded from Market Prism council to the ENTIRE AI Advisor system — operator directive)

## Summary

This is the **operator-mandated closeout verification** for the **entire AI Advisor
system** — both the Market Prism council AND the full AI Advisor suite (Config Advisor,
Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Community strategies,
the proposal/gate infra, and the unified SPA shell). The operator directive: *"the
closeout must be a full end-to-end verification of EVERY feature... not a partial
proof-run, not just the orchestration."* It is **not** a plan to build anything new. It is
the exhaustive acceptance protocol that proves every shipped feature works **live,
end-to-end, on real data** — producer → consumer → route/engine → persistence → rendered
tab — AND that each feature's **documentation matches its verified live behavior**. A doc
claim contradicted by live behavior (the recently-found "macro stub" mislabel class of
defect) is a closeout failure, not a footnote.

The matrix has **two clusters**, each capped by an appropriate live-evidence gate:

- **Cluster 1 — Market Prism council** (F1–F21): 5 lens producers feeding 5 analyst
  agents + 1 synthesizer, an auditable deliberation trail keyed to one `run_id`, exactly
  one `MARKET_PRISM` row, the Overview tab. Capped by the **operator-observed
  multi-analyst run** (the Phase-3 proof run). Supporting infra: audit-log foundation
  (migration 032 + accessors + CLI writer), nightly pipeline (`run_pipeline`), lens
  warehouse (third DB), and the configurable synthesis model (C1 / PR #39).
- **Cluster 2 — AI Advisor suite** (F22–F40): the 6-tab unified SPA and its engines.
  Capped by **live-rendered-tab + real-engine-call evidence** against the running :8090
  daemon + live DB. Each tab's GET render + each POST action route's real engine call is
  exercised on the live page.

This closeout is structured as a **verification matrix** (one row per feature) plus the
**capstone live multi-analyst run** and an explicit **operator sign-off gate** before any
Phase-4 unattended scheduling is enabled.

**Adversarial-completeness stance:** the inventory below was built from the actual code
(`ai_advisor.py`, `advisors/*.py`, `database.py`, `templates/ai_advisor.html`,
`static/ai_advisor.js`, `app.py` routes, the 6 `.claude/agents/prism-*.md` files), the
market-prism + lens + advisor + strategy-builder + community feature-plans, DECISIONS.md,
`docs/generated/`, and the project CLAUDE.md key-files table — then expanded to catch
anything the source-of-truth plans omitted. **Assume a feature was missed until the whole
surface is swept; the matrix below is the proof of the sweep.** Verified facts carry a
`file:line`; anything not directly verifiable in code is labeled `[interpretation]` and
must be confirmed during execution.

**Hollow-producer / stale-doc findings surfaced during enumeration** (each is a verifiable
closeout item, NOT background commentary):
- **HF-1 (hollow wiring): Community-strategies route injection is absent.** The engine
  layer is fully built — `strategy_builder_engine.community_candidate_infos`
  (`:195`) + the `community_candidates` kwarg on `propose_strategies` (`:864`, applied at
  `:921-922`) + `community_strats.load_community_strategies` (`:98`). But the **production
  route never injects community candidates**: `app.py:3437` calls
  `propose_strategies(objective, universe, screen_config, live_returns=[], symphony_id)`
  with NO `community_candidates=` argument, and nothing in `app.py` ever calls
  `load_community_strategies` or `community_candidate_infos`. So in production the Strategy
  Builder runs template-only; the community-strategies feature is reachable only from
  tests. **This contradicts** the CLAUDE.md `community_strats.py` row ("first production
  caller: `propose_strategies` via the `community_candidate_infos` adapter (injected at
  the route boundary)") and DECISIONS.md:633 ("No production caller yet... must not be
  called from production routes until that wiring is in"). The closeout must verify
  whether route-level injection is intended-in-scope (then it is a build gap) or
  deferred (then the CLAUDE.md "injected at the route boundary" claim is a stale-doc
  defect to correct). Tracked as F35.
- **HF-2 (stale doc, 3 modules): C1 not merged** — see F20. PR #39 unifies
  `ADVISOR_SYNTHESIS_MODEL` across THREE LLM modules: `lens_pipeline.py:284` (Haiku),
  `ai_advisor.py:59` (`_CLAUDE_MODEL="claude-opus-4-7"`), `advisor_chat.py:211`
  (`_CHAT_MODEL="claude-opus-4-7"`). Until it lands, three model-literal doc/code states are
  stale and AC-1 gates the F13/F20 pipeline rows AND the F23 config-advisor + F31 chat rows.

---

## Feature Inventory (what "EVERY feature in the AI Advisor system" means)

Enumerated and cross-verified against source on branch base `origin/main @ 348dc26`.
**40 verification-bearing features** across two clusters: Cluster 1 (Market Prism council,
F1–F21, 6 groups) and Cluster 2 (AI Advisor suite, F22–F40, 8 groups). The existing
`market-prism-phase3-observed-proof-run.md` plan covers only the council capstone run
(its AC-1..AC-5) and nothing in Cluster 2. This closeout is a strict superset.

### Cluster 1 — Market Prism council (F1–F21)

| # | Group | Feature |
|---|-------|---------|
| F1 | Lens producers | Technicals lens (`lens_technicals._fetch_technicals` → `_build_technicals_section`) |
| F2 | Lens producers | Sentiment/GDELT lens (`lens_gdelt._fetch_gdelt_sentiment` + artlist → `_build_sentiment_section`) |
| F3 | Lens producers | Derivatives lens (`lens_options_proxy._fetch_options_proxy` → `_build_derivatives_section`) **+ VIX/VXV freshness guard** |
| F4 | Lens producers | Macro lens (FRED series fetch → `_build_macro_section`) |
| F5 | Lens producers | Fundamentals lens (`_fetch_fundamentals_for_ticker` + portfolio fan-out → `_build_fundamentals_section`) |
| F6 | Lens infra | Universe floors: `lens_technicals._PROXY_UNIVERSE` + `_FUNDAMENTALS_PROXY_UNIVERSE` (off-hours non-empty universe) |
| F7 | Lens infra | Honest-availability lens-block contract (`{lens, available, reason, payload, sources}`, D-1 `type(exc).__name__`) |
| F8 | Lens infra | Citation/source validation (`build_citation`) across lenses |
| F9 | Warehouse | Lens Data Warehouse third DB (`lens_warehouse.init_warehouse_db` / `persist_lens_snapshot` / `get_lens_snapshots`, `_strip_secrets`, pytest sentinel) |
| F10 | Warehouse | Warehouse wiring: sentiment (GDELT) + macro (FRED) persist after each fetch |
| F11 | Audit-log | Migration 032 `prism_audit_log` + `insert_prism_audit_entry` / `get_prism_audit_for_run` |
| F12 | Audit-log | Agent-callable CLI writer `advisors/prism_audit_write.py` (STDIN content, prints row id, D-1) |
| F13 | Nightly pipeline | `lens_pipeline.run_pipeline` 4-pass (isolation → citation validation → synthesis → MARKET_PRISM persistence) |
| F14 | Nightly pipeline | 03:00 scheduler wiring (`run_scheduler` / daemon thread, CC-2 lazy import) |
| F15 | Council agents | 5 analyst agents (`prism-{technicals,sentiment,derivatives,macro,fundamentals}-analyst`, model=opus) |
| F16 | Council agents | Synthesizer lead (`prism-synthesizer`): run_id, kickoff, clarifying Q&A, conditional debate ≤3, integrated synthesis |
| F17 | Council agents | Audit-DB-as-source-of-truth protocol (synthesizer derives availability from audit rows, not inbox) |
| F18 | Persistence | Exactly one `MARKET_PRISM` `advisor_observations` row per `run_id` (`insert_advisor_observation`, `is_advisory_only=1`) |
| F19 | Overview tab | Market Prism block render (`templates/ai_advisor.html`: sentiment chip, rationale, per-lens digest, cited sources, empty state) + `get_latest_market_prism_summary()` + `app.py` prefetch |
| F20 | Model config | C1 `ADVISOR_SYNTHESIS_MODEL` env var (default Opus 4.8) — **PR #39, OPEN, unmerged** |
| F21 | Capstone | Observed multi-analyst proof run: real deliberation, complete audit trail, one MARKET_PRISM row, rendered Overview, operator sign-off |

### Cluster 2 — AI Advisor suite (F22–F40)

| # | Group | Feature |
|---|-------|---------|
| F22 | Config Advisor core | `assemble_advisor_context` (Composer hash-not-name, `autotune_run` honoring, allowlisted context surface) |
| F23 | Config Advisor core | `request_suggestions` (Claude call, D-1 all-error-paths `type(exc).__name__`) |
| F24 | Config Advisor core | `build_assessment_from_context` (per-symphony informative empty-state; `oos_alpha=None` ≠ error) |
| F25 | Config Advisor core | 7-item suggestible allowlist (`_SUGGESTIBLE_ALLOWLIST` = 6 Optuna keys + `MAX_SQUEEZE_FLOOR`) + `enforce_suggestion_allowlist` structural rejection |
| F26 | Config Advisor core | C2 safety gates on accept (allowlist + OOS re-validation + risk-direction cross-check) → `POST /ai-advisor/accept`; reject → `POST /ai-advisor/reject` (no config write) |
| F27 | Config Advisor core | CRRA-EU + Harvey-Liu FDR strictness (empty suggestions are expected, not a bug) |
| F28 | Correlations tab | `correlation_diagnostic.compute_pairwise_correlations` → prefetched in `GET /ai-advisor` (`app.py:2910`); rendered in the Correlations panel |
| F29 | Asset Swaps tab | `asset_swap_engine.propose_operator_swap` (objective-directed candidates, `_apply_lens_blend` `LENS_BLEND_WEIGHT=0.25`, BHY-FDR gate, `lens_evidence` persistence) → `POST /ai-advisor/asset-swaps/evaluate` (CSRF) |
| F30 | Logic Changes tab | `logic_change_engine.propose_operator_logic_change` (objective-directed logic tweaks, BHY-FDR gate, persistence) → `POST /ai-advisor/logic-changes/evaluate` (CSRF) |
| F31 | Chat tab (M5) | `advisor_chat.explain_artifact` explain-only (artifact allowlist `CHAT_ARTIFACT_ALLOWED_FIELDS` M1–M4+M6+multi-lens, `validate_artifact` re-validation, hard no-trade/no-write) → `POST /ai-advisor/chat/send` |
| F32 | Strategy Builder | `strategy_builder_engine.propose_strategies` (T1–T7 templates, single-batch FDR `evaluate_candidate_batch`, `ScreenConfig` post-gate screens, persistence) → `POST /ai-advisor/strategy-builder/run` (CSRF, advisory-only) |
| F33 | Strategy Builder | `symphony_schema` (never-raising `validate_tree`/`lint_tree`/`extract_tickers`/`render_rules_text` + 10 constructors) |
| F34 | Strategy Builder | `composer_backtest_client.run_backtest` (1 req/s rate limit, 429 backoff) |
| F35 | Community strategies | `community_strats.load_community_strategies` (atlas_cache weekly-TTL bill protection, structural-hash dedup, sharpe filter) + `community_candidate_infos` adapter — **HF-1: no production route caller (hollow in prod)** |
| F36 | Proposal/gate infra | `backtest_gate_engine.evaluate_candidate_batch` (BHY/Yekutieli FDR across the FULL candidate batch — anti-overfit invariant) |
| F37 | Proposal/gate infra | `acceptance_gate.py` (reusable overfitting acceptance gate, shared by autotuner + advisor proposal suite) |
| F38 | SPA shell | Unified `templates/ai_advisor.html` — 6 in-place tabs in one server render; `static/ai_advisor.js` `initTabSwitcher` |
| F39 | SPA shell | The 5 GET sub-routes 302-redirect to `/ai-advisor` (`/correlations`, `/asset-swaps`, `/logic-changes`, `/chat`, `/strategy-builder`) |
| F40 | SPA shell | CSRF enforcement on all POST action routes (`accept`, `reject`, `suggest`, `*/evaluate`, `chat/send`, `strategy-builder/run`); advisory-only — none in `_SETTINGS_WRITE_ALLOWLIST` |

**Verified source anchors (sample, full anchors in the matrix):**
- 5 lens builders: `ai_advisor.py:456` (technicals), `:530` (sentiment), `:651` (derivatives), `:732` (macro), `:1076` (fundamentals).
- 5-lens set + `_call_lens_section`: `advisors/lens_pipeline.py:38-71`.
- Freshness guard: `advisors/lens_options_proxy.py:347-366` (`reason="stale_data"`).
- Audit accessors: `database.py:1217` (`insert_prism_audit_entry`), `:1252` (`get_prism_audit_for_run`), `:1180` (`get_latest_market_prism_summary`), `:1053` (`insert_advisor_observation`).
- Warehouse: `advisors/lens_warehouse.py:110/127/181`; sentiment+macro wiring `ai_advisor.py:585-630` / `:757-855`.
- CLI writer: `advisors/prism_audit_write.py` (whole file).
- Overview prefetch: `app.py:2948-3019`; render block `templates/ai_advisor.html:942-976`.
- 6 council agents: `.claude/agents/prism-synthesizer.md` + 5 `prism-*-analyst.md` (all `model: opus`).
- C1 unmerged: `advisors/lens_pipeline.py:284` still hardcodes `claude-haiku-4-5-20251001`; PR #39 (`feat/advisor-synthesis-model-config`) OPEN.

**Cluster 2 — AI Advisor suite anchors:**
- Routes: `GET /ai-advisor` (`app.py:2848`); GET redirects `/correlations:3023`, `/asset-swaps:3033`, `/logic-changes:3174`, `/chat:3794`, `/strategy-builder:3381`; POST `/asset-swaps/evaluate:3042`, `/logic-changes/evaluate:3183`, `/strategy-builder/run:3394`, `/suggest:3619`, `/accept:3686`, `/reject:3762`, `/chat/send:3803`.
- Engine call sites (live consumers — none hollow except F35): `compute_pairwise_correlations` ← `app.py:2910`; `propose_operator_swap` ← `:3114`; `propose_operator_logic_change` ← `:3264`; `propose_strategies` ← `:3437`; `explain_artifact` ← `:3888`.
- Allowlist: `_SUGGESTIBLE_ALLOWLIST` = 6 Optuna keys ∪ `MAX_SQUEEZE_FLOOR` (`ai_advisor.py:1718`); `enforce_suggestion_allowlist:1743`; `_UNTUNED_SUGGESTIBLE_KEY="MAX_SQUEEZE_FLOOR":73`.
- Asset swap: `LENS_BLEND_WEIGHT=0.25` (`asset_swap_engine.py:78`), `_apply_lens_blend:372`.
- Strategy builder: `propose_strategies:864` (with `community_candidates` kwarg), `community_candidate_infos:195`, `MAX_COMMUNITY_CANDIDATES_PER_RUN=20:44`; gate `backtest_gate_engine.evaluate_candidate_batch` (BHY/Yekutieli FDR); rate limit `composer_backtest_client.py:30` (1 req/s).
- Chat: `CHAT_ARTIFACT_ALLOWED_FIELDS:74`, `validate_artifact:167`, `explain_artifact:337`.
- Community: `load_community_strategies:98` (atlas_cache `cached_pull:156`, structural-hash `_composition_hash:63`).
- **HF-1 (hollow):** `app.py:3437` `propose_strategies(...)` passes NO `community_candidates`; no `load_community_strategies`/`community_candidate_infos` call exists anywhere in `app.py`.

---

## Acceptance Criteria

Each AC is a closeout gate. The closeout PASSES only when **every** AC passes; any FAIL
loops back to the owning feature's cycle (do not paper over a degenerate result).

- [ ] **AC-1 (Dependency precondition — gates BOTH clusters):** C1 (PR #39) is merged to
  origin and the running :8090 daemon is on the deployed code that includes it. PR #39
  touches **THREE** LLM modules, so C1 unifies the model env var across the council pipeline
  AND the suite's LLM features: at base `348dc26` the hardcoded literals are
  `lens_pipeline.py:284 = "claude-haiku-4-5-20251001"` (pipeline synthesis — F13/F20),
  `ai_advisor.py:59 = _CLAUDE_MODEL "claude-opus-4-7"` (config-advisor `request_suggestions`
  — F23), and `advisors/advisor_chat.py:211 = _CHAT_MODEL "claude-opus-4-7"` (chat — F31).
  After merge+deploy, NONE of these literals governs the production path —
  `ADVISOR_SYNTHESIS_MODEL` (default Opus 4.8) does. Verified by reading all three deployed
  modules on the running tree + a config probe. **This means AC-1 is a precondition for the
  F13/F20 pipeline rows AND the F23 config-advisor + F31 chat rows.**
- [ ] **AC-2 (Per-lens live E2E):** For EACH of the 5 lenses (F1–F5), a live call through
  the real producer reaches `available=True` with **real, non-stub values** when its data
  source is reachable AND keys are present; AND the honest-degradation path returns
  `available=False` with a `type(exc).__name__`-only / informative reason when the source
  is unreachable or a key is absent. Both arms observed (not inferred).
- [ ] **AC-3 (Universe floors):** With live `logic_holdings` empty (off-hours / flat), the
  technicals and fundamentals lenses still receive a non-empty universe from their proxy
  floors (F6), so breadth/fan-out are computed on a real basket — not hollow `available=False`.
- [ ] **AC-4 (Warehouse persistence):** After a live sentiment and macro fetch, new rows
  appear in the third DB (`alphabot_warehouse.db`) via `get_lens_snapshots`; secrets are
  stripped from `raw_json`; the pytest sentinel blocks opening the real warehouse under
  pytest (F9, F10).
- [ ] **AC-5 (Audit-log foundation):** The CLI writer (F12) writes a row and prints its id;
  `get_prism_audit_for_run` returns it; migration 032 is the last wired migration (F11).
- [ ] **AC-6 (Nightly pipeline non-hollow):** A live `run_pipeline()` (non-dry-run) writes
  exactly one `MARKET_PRISM` row, runs all 5 lenses with per-lens isolation, validates
  citations, and synthesizes; when lenses have real data the verdict is a real sentiment,
  NOT `limited-inputs` (F13). The 03:00 scheduler is wired and would fire (F14).
- [ ] **AC-7 (Council deliberation live):** The multi-analyst council (F15–F17) runs on
  real data under observation: all 5 analysts file `initial_read` rows; clarifying Q&A
  occurs where relevant; debate fires ONLY on genuine disagreement (≤3 rounds) and is
  ABSENT when analysts converge; the synthesizer derives availability from audit rows, not
  its inbox.
- [ ] **AC-8 (One row per run):** Exactly one `MARKET_PRISM` row exists for the capstone
  `run_id` (F18). A retry/duplicate is a defect to fix before sign-off.
- [ ] **AC-9 (Overview renders, eyes-on):** The Overview tab renders the produced report on
  the live :8090 page; the PM Reads the screenshot **with its own eyes** and describes the
  sentiment chip, rationale, per-lens digest, and cited sources BEFORE asserting
  correctness; the informative empty-state renders when no row exists (F19).
- [ ] **AC-10 (Doc-accuracy sweep):** For EVERY feature F1–F20, the feature's documentation
  (`docs/generated/`, the CLAUDE.md key-files row, DECISIONS.md) matches the verified live
  behavior. Any contradicted claim (e.g. a "stub" label on a live producer) is filed and
  corrected as part of closeout — a doc/behavior mismatch is a closeout FAIL.
- [ ] **AC-11 (Operator sign-off gate):** The PM surfaces the capstone artifacts (rendered
  Overview screenshot + full audit-trail dump + lens-coverage/debate/spend note) to the
  operator and receives explicit sign-off. Phase-4 unattended scheduling stays
  **hard-blocked** until sign-off is received.
- [ ] **AC-12 (No execution-path contamination):** No closeout step touches
  `LIVE_EXECUTION`, trade orders, or position state. Every verified surface is advisory-only
  (`is_advisory_only=1` on the MARKET_PRISM and all advisor-observation rows).

**Cluster 2 — AI Advisor suite ACs:**

- [ ] **AC-13 (Every tab renders live):** All 6 SPA tabs (Overview, Correlations, Asset
  Swaps, Logic Changes, Chat, Strategy Builder) render on the live `GET /ai-advisor` page;
  tab switching is in-place; `static/ai_advisor.js` passes `node --check`; each panel is
  confirmed by an eyes-on screenshot read (F28/F38). The 5 GET sub-routes 302-redirect to
  `/ai-advisor` (F39).
- [ ] **AC-14 (Every action route drives its real engine live):** Each POST action route
  invokes its real engine on the live DB with a valid CSRF token and returns the expected
  shape: suggest→suggestions, accept→3-gate apply, reject→no-write, asset-swaps/evaluate→
  gated swap candidates + persisted `ASSET_SWAP` row, logic-changes/evaluate→gated logic
  tweaks + persisted `LOGIC_CHANGE` row, chat/send→explanation, strategy-builder/run→
  gated survivor/rejected/FDR JSON + persisted survivors (F23/F26/F29/F30/F31/F32).
- [ ] **AC-15 (Safety boundaries hold live):** the 7-item allowlist rejects out-of-scope
  keys (F25); the C2 accept gates block on failure (F26); the chat is explain-only — no
  trade/no-write/no OOS-revalidation/no-backtest (F31); CSRF rejects tokenless POSTs (F40);
  NO advisor route is in `_SETTINGS_WRITE_ALLOWLIST` and none touches `LIVE_EXECUTION`.
- [ ] **AC-16 (FDR / overfit invariants hold live):** the BHY/Yekutieli FDR gate runs
  across the FULL candidate batch (template + any community together — anti-overfit
  invariant), screens never shrink the gate input, and the same `acceptance_gate` governs
  autotuner + advisor suite (F36/F37). The Composer backtest client paces at ≤1 req/s (F34).
- [ ] **AC-17 (Hollow-producer finding resolved — HF-1):** the community-strategies
  production gap is explicitly adjudicated with the operator: EITHER route-level injection
  is added (then F35 is a build gap to fix on its own cycle) OR it is deferred-by-design
  (then the CLAUDE.md "injected at the route boundary" claim is corrected to match
  reality). The closeout does NOT silently pass F35 as if community strats were live in
  prod. This AC is satisfied by the adjudication + the doc correction, not by a green test.
- [ ] **AC-18 (Suite doc-accuracy):** for every Cluster-2 feature F22–F40, the docs
  (`docs/generated/`, CLAUDE.md key-files, DECISIONS.md) match verified live behavior;
  HF-1 and any other contradiction is filed + corrected (subsumes AC-10 for the suite).

---

## Architecture

This feature introduces **no new code**. It is an observed operational verification that
exercises the shipped AI Advisor system (Market Prism council + the full advisor suite) on
real data, plus a doc-accuracy reconciliation pass. The PM (or read-only verifier agents
for the non-council probes) drives it; the capstone council run is an operator-gated Claude
Code Agent Team run. Cluster 2 (the suite) is verified by live-rendered-tab +
real-engine-call evidence against the running :8090 daemon + live DB.

**Two execution layers exist and BOTH are verified (they are not the same path):**
1. **Programmatic Cycle-4 path** — `lens_pipeline.run_pipeline()` calls each
   `_build_*_section()` directly, synthesizes via `_synthesize_via_claude` (model = C1
   env var post-PR-#39), writes the `MARKET_PRISM` row. This is what the 03:00 daemon
   thread fires. (F13/F14)
2. **Multi-analyst council path** — the `prism-synthesizer` Agent Team where each analyst
   pulls its lens via `_call_lens_section` and writes its own audit trail; the synthesizer
   writes the `MARKET_PRISM` row. This is the human-deliberation layer that
   `market-prism-overview.md` says *"replaces how that row is produced."* (F15–F18)

   **[interpretation]** These two writers both target the same `MARKET_PRISM` row family.
   The closeout must confirm which path is authoritative for the nightly row post-Epic-A
   and that they do not race or double-write on the same night. Confirm during execution.

**Live environment under verification:**
- Running daemon at `:8090` on the **deployed** post-PR-#39 code (AC-1).
- Live state DB (`alphabot_state.db`) — `advisor_observations` + `prism_audit_log`.
- Third DB `alphabot_warehouse.db` — lens snapshots.
- Real external APIs: GDELT (key-less), FRED (`FRED_API_KEY`), SEC EDGAR (UA header),
  Alpaca (technicals bars), Anthropic (Opus 4.8 for the council; synthesis model for the
  pipeline).

**Integration points exercised (read-only / advisory writes only):**
`ai_advisor._build_*_section` · `advisors/lens_*.py` producers · `advisors/lens_pipeline.py`
· `advisors/lens_warehouse.py` · `advisors/prism_audit_write.py` · `database.py` accessors ·
`templates/ai_advisor.html` Overview block · `app.py` prefetch + 03:00 scheduler · the 6
`.claude/agents/prism-*.md` role files.

## Design-System Mapping

N/A for the backend producers/infra. The one UI surface — the Overview tab Market Prism
block (`templates/ai_advisor.html:942-976`) — is verified by a live render + eyes-on
screenshot read (AC-9), asserting the design-system sentiment-chip classes
(`prism-sentiment-chip--{risk-on,risk-off,neutral,limited-inputs}`) render, not specific
RGB values.

---

## Verification Matrix — Cluster 1: Market Prism council (F1–F21)

Columns: **Feature** | **Producer/Consumer files** | **Live E2E check** | **Expected
evidence (PASS)** | **Doc-accuracy check** | **Pass/Fail**. (Cluster 2 — the AI Advisor
suite, F22–F40 — follows the capstone block below.)

> Run order: Group A (lens producers + infra) and Group B (warehouse/audit/pipeline) are
> independent probes runnable before the council run. Group C (council) is the capstone and
> depends on A/B passing. AC-1 (C1 merge + deploy) gates everything. Cluster 2 (F22–F40)
> is independently runnable once the daemon is on the deployed post-#39 code.

### Group A — Lens producers + lens infra (F1–F8)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F1 Technicals** | `advisors/lens_technicals.py` `_fetch_technicals` / `_PROXY_UNIVERSE`; `ai_advisor._build_technicals_section` (`:456`) | On the deployed tree, call `ai_advisor._build_technicals_section()` against live Alpaca; then force the failure arm (break Alpaca creds / empty universe). | `available=True` with real `ma_posture`, `breadth` (0..1), `momentum` over the proxy basket; AND failure arm returns `available=False` + `reason` (no `str(exc)`), no traceback. | CLAUDE.md `lens_technicals` row + DECISIONS lens-technicals entry match: 50/200 SMA, breadth excludes insufficient-history tickers, 20-day momentum, proxy floor `_PROXY_UNIVERSE`. Flag any "stub" wording. | |
| **F2 Sentiment/GDELT** | `advisors/lens_gdelt.py` `_fetch_gdelt_sentiment` + artlist; `ai_advisor._build_sentiment_section` (`:530`) | Call `_build_sentiment_section()` live (GDELT key-less); observe tone + artlist citations; force artlist-only and tone-only arms (per-source isolation). | `available=True` with a numeric tone and ≥1 validated source; per-source isolation proven (one signal can fail, the other succeeds); `reason` is type-name only on full failure. | `.claude/gdelt-contract.md` + CLAUDE.md sentiment wiring match live shape (timelinetone endpoint, normalization). Confirm GDELT key-less claim. | |
| **F3 Derivatives + freshness guard** | `advisors/lens_options_proxy.py` `_fetch_options_proxy` (`:313`), freshness guard (`:347-366`); `ai_advisor._build_derivatives_section` (`:651`) | Call `_build_derivatives_section()` live with `FRED_API_KEY` set; THEN reproduce the stale-data arm (point at an old observation / simulate >threshold age) and confirm `reason="stale_data"`, `available=False`. | Fresh path: `available=True` with `vix_level`, `vix_term_structure.regime`, `risk_read`, `as_of_date` recent; stale path: `available=False` `reason="stale_data"` — stale FRED data NOT served as current (the PR #37 fix). | CLAUDE.md derivatives row + DECISIONS/PR-#37 entry describe the freshness guard accurately (threshold, `stale_data` reason). | |
| **F4 Macro** | FRED series fetch `ai_advisor._build_macro_section` (`:732`); `_FRED_SERIES` | Call `_build_macro_section()` live with `FRED_API_KEY`; then unset the key for the degradation arm. | `available=True` with ≥1 FRED series (10y/UNRATE/CPI/FedFunds) carrying value+date + clickable source per series; key-absent arm → `available=False` with the informative "register free at fred.stlouisfed.org" reason. **NOT a stub.** | **HIGH-RISK doc check (the "macro stub" mislabel class):** confirm no doc anywhere calls macro a stub/placeholder — it is a live FRED producer (`:780-869`). File + correct any stale "stub" wording. | |
| **F5 Fundamentals fan-out** | `ai_advisor._fetch_fundamentals_for_ticker` + `_build_fundamentals_section` (`:1076`), `_FUNDAMENTALS_PROXY_UNIVERSE` | Call `_build_fundamentals_section()` (ticker=None, portfolio path) live against SEC EDGAR; verify fan-out over holdings∪proxy; confirm single-ticker path (`ticker="AAPL"`) byte-preserved; force all-fail arm. | Portfolio path: `available=True` with `per_ticker_results` for ≥1 company ticker + deduped SEC sources; per-ticker honest degradation (one ticker fails, others resolve); all-fail → `available=False`. Single-ticker path unchanged (PR #38 fix — dead lens now live). | CLAUDE.md fundamentals row (DE-FUND-001) matches: 8 large-cap COMPANY tickers (not ETFs), bounded fan-out, no invented composite ratios. | |
| **F6 Universe floors** | `lens_technicals._PROXY_UNIVERSE`; `ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE` | With live `logic_holdings` empty (verify via `database.load_state()`), confirm both lenses still build a real universe (proxy floor applied). | Technicals breadth + fundamentals fan-out computed on the proxy basket despite empty holdings — no hollow `available=False` from an empty universe (DE-TECH-002). | CLAUDE.md describes both proxy floors and the off-hours rationale; confirm the floor tickers listed match code. | |
| **F7 Honest-availability contract** | All 5 builders; `lens_pipeline._call_lens_section` (`:54`) D-1 wrapper | Across F1–F5 failure arms, assert the fixed 5-key contract `{lens, available, reason, payload, sources}` and that `reason` is only `type(exc).__name__` (no message/URL/key leak — FRED embeds the key in the URL). | Every failure arm: 5-key dict, `payload=None`, `sources=[]`, `reason` a bare type name or curated string — no `str(exc)`, no API key, no traceback. | DECISIONS DE-ML-001 (honest-availability contract) matches live behavior. | |
| **F8 Citation validation** | `ai_advisor.build_citation`; `lens_pipeline._validate_and_filter_sources` (`:128`) | Feed a malformed citation through a live lens; confirm it is dropped, not surfaced. | Only well-formed `{title,url,published,lens}` sources appear in the Overview "cited sources"; malformed entries silently dropped. | CLAUDE.md / DECISIONS citation convention (DE-ML-002, `raw_response` JSON) matches. | |

### Group B — Warehouse, audit-log foundation, nightly pipeline (F9–F14)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F9 Lens Data Warehouse** | `advisors/lens_warehouse.py` `init_warehouse_db` (`:110`), `persist_lens_snapshot` (`:127`), `get_lens_snapshots` (`:181`), `_strip_secrets` | `init_warehouse_db()` on the live `alphabot_warehouse.db`; `persist_lens_snapshot` with a payload containing a fake `api_key`; read it back via `get_lens_snapshots`; confirm the pytest sentinel raises when opening the real warehouse under pytest. | Row round-trips; `raw_json` has the secret key stripped (recursive `_strip_secrets`); append-only; WAL; sentinel blocks pytest from the real file. **Third DB confirmed distinct** from state + optimization (no cross-DB join). | CLAUDE.md `lens_warehouse` row (DW-1) matches: third DB, append-only, secret-strip set, engine-agnostic `raw_json`. | |
| **F10 Warehouse wiring** | `ai_advisor._build_sentiment_section` (`:585-630`) + `_build_macro_section` (`:757-855`) persist calls | After live F2 + F4 calls, query `get_lens_snapshots("sentiment")` and `("macro")`; confirm new rows for this run. | Sentiment (GDELT) and macro (FRED) each wrote a snapshot row (available True AND False paths both persist); off-execution-path lazy import (no module-level warehouse import). | CLAUDE.md states sentiment+macro are wired "non-hollow"; confirm no OTHER lens claims warehouse wiring it doesn't have. | |
| **F11 Migration 032 + accessors** | `database.py` migration 032, `insert_prism_audit_entry` (`:1217`), `get_prism_audit_for_run` (`:1252`) | `python -c "import database; print(database._MIGRATION_FILES[-1])"` → `032_prism_audit_log.sql`; insert + read-back a row on the live DB. | Last wired migration is 032; round-trip returns the row keyed by `run_id`, ordered append-only. | CLAUDE.md database row lists migration 032 + both accessors with correct signatures. | |
| **F12 CLI writer** | `advisors/prism_audit_write.py` (whole file) | `echo "test read" \| python -m advisors.prism_audit_write --run-id <rid> --role technicals_analyst --phase initial_read` with `DB_PATH` set to live DB; confirm it prints a positive row id; confirm a missing-arg / bad invocation prints only a type name (D-1) and exits non-zero. | STDOUT = positive integer row id; the row is readable via `get_prism_audit_for_run`; error arm leaks no traceback/message. | CLAUDE.md `prism_audit_write` row matches the `--run-id/--role/--phase` + STDIN contract. | |
| **F13 Nightly pipeline 4-pass** | `advisors/lens_pipeline.run_pipeline` (`:314`), `_collect_lenses` (`:98`), `_synthesize_via_claude` (`:243`) | Run `run_pipeline()` (non-dry-run) live with all keys present; inspect the returned dict + the written `MARKET_PRISM` row. | Exactly one MARKET_PRISM row written; all 5 lenses attempted with per-lens isolation (`lenses_attempted=5`); citations validated; a REAL `verdict` (not `limited-inputs` when lenses have data); `available_lens_count` reflects reality. | CLAUDE.md `lens_pipeline` row + DECISIONS DE-CY4-001 match; **confirm the synthesis-model line reflects C1 (env var), not the old hardcoded Haiku** (AC-1 dependency). | |
| **F14 03:00 scheduler** | `app.py` `run_scheduler` (`:437-443`) → `_run_lens_pipeline` (`:425`) → daemon thread → `_lens_pipeline_worker` (`:410`) → `run_pipeline` lazy import (`:417`) | Confirm the daemon registers the 03:00 Prism job (`schedule.every().day.at("03:00").do(_run_lens_pipeline)`, `app.py:443`) on the running tree; check daemon log for the scheduled entry. | The 03:00 job is registered and invokes `run_pipeline` via a daemon thread + lazy import (never module-level — CC-2), so the scheduler thread returns immediately. | CLAUDE.md states 03:00 daily via `run_scheduler()`; confirm time (`03:00`, off-hours, no overlap with `:00` execution path) + lazy-import boundary documented accurately. | |

### Group C — Council agents, persistence, Overview, model config (F15–F20)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F15 5 analyst agents** | `.claude/agents/prism-{technicals,sentiment,derivatives,macro,fundamentals}-analyst.md` | During the capstone run (F21), each analyst pulls its lens via `_call_lens_section`, forms a real read, and files `phase=initial_read` to the audit log. | 5 distinct `initial_read` audit rows (one per analyst role), each reflecting its real lens data; model=opus confirmed in each agent file. | Each analyst file's described lens + role string matches its audit `agent_role`; no analyst references a lens it doesn't own. | |
| **F16 Synthesizer orchestration** | `.claude/agents/prism-synthesizer.md` | During F21: synthesizer generates `run_id`, kicks off all 5, facilitates clarifying Q&A, decides debate, integrates, writes synthesis + MARKET_PRISM row. | `run_id` immutable across the run; `phase=synthesis` row present; conditional-debate protocol respected (see F17); a genuinely integrated read (cross-lens reasoning, not a concatenation — AC-7 in phase-2 plan). | `prism-synthesizer.md` debate protocol (≤3 rounds, clarifications ≠ debate) matches the runbook + phase-2 plan. | |
| **F17 Debate/clarification protocol** | synthesizer + analysts; audit `phase` tags | In F21: confirm clarifications are tagged `phase=clarification`, debate rounds `phase=debate_round_N` ONLY on genuine disagreement, and ABSENT when analysts converge. | Audit trail shows correct phase tags; NO spurious `debate_round_*` rows when reads converge; ≤3 debate rounds if disagreement; synthesizer derived availability from audit rows (F17 protocol), not inbox. | Runbook step 4/7 + phase-2 AC-3/AC-4/AC-5 match observed trail behavior. | |
| **F18 One MARKET_PRISM row/run** | `database.insert_advisor_observation` (`:1053`, `is_advisory_only=1`) | After F21: `SELECT count(*) ... WHERE advisor_role='MARKET_PRISM' AND raw_response run_id=<rid>`. | Exactly 1 row; `is_advisory_only=1`; `verdict` matches synthesizer's `overall_sentiment`; `run_id` matches the audit trail. | DECISIONS DE-ML-003 (MARKET_PRISM advisory-only, DB-enforced flag) matches. | |
| **F19 Overview tab render** | `templates/ai_advisor.html:942-976`; `database.get_latest_market_prism_summary` (`:1180`); `app.py:2948-3019` prefetch | Load `GET /ai-advisor` on live :8090 after F21; capture a screenshot; **Read it with the Read tool (eyes-on)**; describe chip + rationale + per-lens digest + cited sources. Also verify the empty-state arm (no row) renders informatively. | The rendered block shows the capstone sentiment chip (correct semantic class), rationale text, per-lens digest (5 lenses with available flags), and clickable cited sources; empty-state arm renders the informative message, not a blank/error. PM describes the screenshot before asserting. | CLAUDE.md `templates/ai_advisor.html` Overview row matches what renders (chip/rationale/digest/sources/empty-state). | |
| **F20 C1 model config (3 modules)** | PR #39 unifies `ADVISOR_SYNTHESIS_MODEL` across `advisors/lens_pipeline._synthesize_via_claude` (hardcoded Haiku `:284`), `ai_advisor.py` (`_CLAUDE_MODEL` `:59`), `advisors/advisor_chat.py` (`_CHAT_MODEL` `:211`) | After PR #39 merge+deploy: probe that ALL THREE paths read `ADVISOR_SYNTHESIS_MODEL` (default Opus 4.8) and no hardcoded literal governs prod; confirm tests don't fire real Opus. | All 3 deployed modules read the env var; default resolves to Opus 4.8; no `claude-haiku-4-5-20251001` / `claude-opus-4-7` literal on any prod LLM path. | **THREE stale doc lines to reconcile** (all currently wrong until #39 lands): CLAUDE.md `lens_pipeline` "Claude Haiku synthesis", and any `ai_advisor.py` / `advisor_chat.py` model-literal references in DECISIONS / `docs/generated/`. DECISIONS + `docs/generated/` must document `ADVISOR_SYNTHESIS_MODEL` + default. | |

### Capstone — Phase-3 observed multi-analyst run (F21)

| Feature | Live E2E check | Expected evidence (PASS) | Doc-accuracy | P/F |
|---|---|---|---|---|
| **F21 Observed proof run** | PM drives the real `prism-synthesizer` Agent Team once on real data under direct observation (single `run_id`, real Opus spend). Follows `market-prism-runbook.md` steps 1–8. | (1) One real integrated `MARKET_PRISM` row — NOT "Synthesis unavailable", NOT a stub, NOT degenerate `limited-inputs` when lenses have data; (2) `get_prism_audit_for_run(run_id)` returns the full trail (≥5 initial_read + clarifications + any debate + 1 synthesis); (3) exactly one row per run_id; (4) Overview renders it (F19, eyes-on); (5) PM surfaces both artifacts + operator note (lens coverage, debate summary, Opus spend) and receives sign-off. | The runbook + phase-3 plan describe exactly this run; reconcile the overview epic-status (🟡 in progress) to reflect closeout outcome. | |

---

## Verification Matrix — Cluster 2: AI Advisor suite (F22–F40)

Capped by **live-rendered-tab + real-engine-call evidence** against the running :8090
daemon + live DB. For each tab: load `GET /ai-advisor`, switch to the tab in-place, observe
the render (eyes-on screenshot where there is a visual surface), and drive each POST action
route with a real engine call. CSRF tokens required on POST routes (the daemon enforces
them; `_disable_csrf_for_tests` only applies under pytest, so live calls must carry a token).

### Group H — Config Advisor core (F22–F27)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F22 assemble_advisor_context** | `ai_advisor.assemble_advisor_context` (`:1430`); route resolves NAME→hash from `bot_state`, passes `composer_symphony_id` | For a real live symphony, drive the context assembly via the suggest route; confirm the Composer `/score` call uses the **hash**, not the display name (the hash-not-name rule). | Context assembles without an HTTP 400 from Composer; passing a name would 400 — confirm a real hash was sent; `autotune_run` honoring (pre-fetched row skips internal fetch). | CLAUDE.md `ai_advisor.py` row + Architecture Constraint #6 (hash-not-name) match live behavior. | |
| **F23 request_suggestions** (C1-gated) | `ai_advisor.request_suggestions` (`:1595`), model `_CLAUDE_MODEL` (`:59`, C1) → `POST /ai-advisor/suggest` (`app.py:3619`) | Call `POST /ai-advisor/suggest` on the live page for a real symphony; force an error arm (no API key / bad symphony). On the deployed post-#39 tree, confirm the model comes from `ADVISOR_SYNTHESIS_MODEL`, not the hardcoded `claude-opus-4-7`. | Returns suggestions JSON on success; on any error returns `type(exc).__name__` only (D-1) — no `str(exc)`, no key, no traceback; the LLM model is env-driven (C1), no `_CLAUDE_MODEL="claude-opus-4-7"` literal governs prod. | CLAUDE.md "request_suggestions (D-1 fully honored...)" matches; **AC-1 gates this row** — the `ai_advisor.py` model literal is reconciled by C1 (F20). | |
| **F24 build_assessment_from_context** | `ai_advisor.build_assessment_from_context` (`:1368`) | On a symphony where all trials were haircut-rejected, observe the assessment block. | The block renders an **informative** empty-state explaining `oos_alpha=None` (haircut-rejected, not an error) — NOT a blank or an error toast. | CLAUDE.md gotcha "AI Advisor empty suggestions... Expected" + `build_assessment_from_context` per-symphony empty-state match. | |
| **F25 7-item allowlist** | `_SUGGESTIBLE_ALLOWLIST` (`:1718`), `enforce_suggestion_allowlist` (`:1743`) | Submit (or simulate) an accept whose `config_key` is OUTSIDE the 7-item allowlist; confirm structural rejection. | Any key not in {6 Optuna search-space keys, `MAX_SQUEEZE_FLOOR`} is rejected before any write; `LIVE_EXECUTION`/credential keys never accepted. | CLAUDE.md "7-item suggestible allowlist (6 Optuna search-space keys + MAX_SQUEEZE_FLOOR)" matches the code set exactly. | |
| **F26 C2 safety gates** | `POST /ai-advisor/accept` (`app.py:3686`, "all three C2 safety gates"); `POST /ai-advisor/reject` (`:3762`) | Drive a real accept through the three gates (allowlist + OOS re-validation + risk-direction cross-check); drive a reject. | Accept applies only after all 3 gates pass and writes the allowlisted .env key; a gate failure blocks the write; reject records the rejection with **no** config write. | DECISIONS / CLAUDE.md "C2 safety gates" enumerate the same three gates that fire live. | |
| **F27 FDR strictness** | CRRA-EU + Harvey-Liu FDR (autotuner/`acceptance_gate`); advisor consumes | Confirm that an empty-suggestions result on a strict symphony is the **expected** strict-gate outcome, not a failure. | Empty suggestions accompanied by the assessment explaining strictness (F24); no error state. | Gotcha "CRRA-EU + Harvey-Liu FDR gate is intentionally strict" matches. | |

### Group I — Correlations / Asset Swaps / Logic Changes tabs (F28–F30)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F28 Correlations tab** | `correlation_diagnostic.compute_pairwise_correlations` (`:196`) ← prefetch `app.py:2910` | Load `GET /ai-advisor`, switch to Correlations; with ≥2 live symphonies having return series, observe the rendered correlation matrix. | A real pairwise correlation matrix renders (values in [-1,1]); the informative empty/insufficient-data state renders when <2 series exist. Eyes-on screenshot read. | CLAUDE.md SPA row lists Correlations as a live tab; `correlation_diagnostic` producer documented + matches. | |
| **F29 Asset Swaps tab** | `asset_swap_engine.propose_operator_swap` (`:912`) ← `POST /ai-advisor/asset-swaps/evaluate` (`app.py:3042`, CSRF) | POST a real swap evaluation for a live symphony (with CSRF token); inspect the returned candidates + persisted observation. | Returns objective-directed candidates ranked with the `_apply_lens_blend` blend (weight 0.25); survivors passed the BHY-FDR gate; an `ASSET_SWAP` observation persists with `lens_evidence`+`sources` (`is_advisory_only=1`). | DECISIONS DE-CY3-001 (lens blend, weight 0.25, gate unchanged, persistence contract) matches live. | |
| **F30 Logic Changes tab** | `logic_change_engine.propose_operator_logic_change` ← `POST /ai-advisor/logic-changes/evaluate` (`app.py:3183`, CSRF) | POST a real logic-change evaluation for a live symphony (with CSRF token); inspect candidates + persisted observation. | Returns objective-directed logic tweaks gated by BHY-FDR; a `LOGIC_CHANGE` observation persists (`is_advisory_only=1`); D-1 on error. | CLAUDE.md/DECISIONS logic-change engine docs match the live route behavior. | |

### Group J — Chat tab M5 (F31)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F31 Chat explain-only** (C1-gated) | `advisor_chat.explain_artifact` (`:337`), `validate_artifact` (`:167`), `CHAT_ARTIFACT_ALLOWED_FIELDS` (`:74`), model `_CHAT_MODEL` (`:211`, C1) ← `POST /ai-advisor/chat/send` (`app.py:3803`) | Send a real chat question against a scoped artifact; ALSO attempt an artifact with a field outside the allowlist + a question that tries to trigger a trade/write. On the deployed post-#39 tree, confirm the chat model is env-driven, not the hardcoded `claude-opus-4-7`. | A real LLM explanation returns; the out-of-allowlist field is **stripped** by `validate_artifact` (re-validated inside `explain_artifact`, defense-in-depth); the chat NEVER calls OOS re-validation, `suggest_swaps`, or `run_backtest` and NEVER writes config — the hard explain-only boundary holds; LLM error → 200 JSON `{error}` (D-1); model env-driven (C1), no `_CHAT_MODEL="claude-opus-4-7"` literal in prod. | `m5-chat-hardening.md` + `security-review-m5-chat.md` + CLAUDE.md (M1–M4+M6+multi-lens fields, explain_artifact re-validation) match the live boundary; **AC-1 gates this row** — the `advisor_chat.py` model literal is reconciled by C1 (F20). | |

### Group K — Strategy Builder tab (F32–F34)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F32 propose_strategies** | `strategy_builder_engine.propose_strategies` (`:864`) ← `POST /ai-advisor/strategy-builder/run` (`app.py:3394`, CSRF) | POST a real Strategy Builder run (objective + universe, CSRF token) on the live page; inspect survivor/rejected/FDR JSON + the Strategy Builder tab render. | Returns T1–T7 template candidates backtested + a **single-batch** FDR gate result (`evaluate_candidate_batch` over the full batch); `ScreenConfig` post-gate screens applied; survivors persisted; advisory-only (not in `_SETTINGS_WRITE_ALLOWLIST`); no `LIVE_EXECUTION`. Eyes-on the rendered survivor/rejected cards. | CLAUDE.md `strategy_builder_engine.py` row + phase-2/3/4 contracts match; **confirm the run is template-only in prod (HF-1) and the doc's "injected at the route boundary" claim is reconciled.** | |
| **F33 symphony_schema** | `advisors/symphony_schema.py` (`validate_tree`/`lint_tree`/`extract_tickers`/`render_rules_text` + 10 constructors) | Through F32: confirm the built trees pass `validate_tree` (no HARD errors) and `render_rules_text` produces readable rules; feed a deep/oversized tree to confirm lint-only (not raise). | Trees validate; `lint_tree` returns soft warnings (size/depth caps + unknown indicator fns are lint-only, never raise); deterministic rules text; never-raising on arbitrary input. | CLAUDE.md `symphony_schema.py` row (never-raising, lint-only caps, 10 constructors, depth-230 safe) matches; vocabulary pinned by `strategy-builder-composer-grammar.md`. | |
| **F34 composer_backtest_client** | `composer_backtest_client.run_backtest` (1 req/s, `:30`; 429 backoff `:332`) | Through F32: confirm backtests are paced at ≤1 req/s and a 429 triggers the documented backoff. | Backtest calls respect the Composer 1 req/s limit; a 429 sleeps per `Retry-After`/`_BACKOFF_INTERVALS`; no rate-limit storm. | CLAUDE.md/strategy-builder docs state "1 req/s" — matches the client constant + comment. | |

### Group L — Community strategies + proposal/gate infra (F35–F37)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F35 Community strategies** (HF-1) | `community_strats.load_community_strategies` (`:98`), `community_candidate_infos` (`strategy_builder_engine.py:195`) — **NO production route caller** | Two-part: (a) Verify the engine layer directly — call `load_community_strategies` (atlas_cache weekly TTL: a 2nd call within TTL hits cache, not Mongo; structural-hash dedup; sharpe filter) and adapt via `community_candidate_infos`. (b) **Verify the PRODUCTION GAP:** confirm `app.py:3437` passes NO `community_candidates` and no route fetches Atlas community strats. | (a) loader returns `{available,candidates,stats,source}`, cache protects the provider bill, dedup + sharpe filter work; (b) the Strategy Builder route runs **template-only** in prod — community strats are NOT reachable from the live UI. **This is a finding, not a pass:** decide build-gap vs deferred-by-design with the operator. | **HF-1 / stale-doc:** CLAUDE.md `community_strats.py` row says "first production caller: propose_strategies via the community_candidate_infos adapter (injected at the route boundary)" — FALSE in prod (no route injection). DECISIONS:633 ("no production caller yet") is closer to truth. Reconcile both to the verified state. | |
| **F36 BHY-FDR gate** | `backtest_gate_engine.evaluate_candidate_batch` (BHY/Yekutieli FDR across the FULL batch) | Through F29/F30/F32: confirm the gate runs FDR across the full candidate set (template + any community together), not per-candidate; screens never shrink the gate input. | The FDR correction's N = full candidate count; survivors are the BHY-adjusted significant set; the anti-overfit invariant (gate input = full batch) holds. | CLAUDE.md strategy-builder row + DECISIONS community-wiring (single-batch FDR invariant) match; gate applies BHY/Yekutieli (Harvey-Liu 2015). | |
| **F37 acceptance_gate** | `acceptance_gate.py` (shared by autotuner + advisor proposal suite) | Confirm the same reusable gate object governs both the autotuner and the advisor proposal suite (one acceptance contract, not two divergent ones). | The advisor suite and autotuner invoke the same `acceptance_gate` logic; no divergent duplicate gate. | CLAUDE.md `acceptance_gate.py` row ("used by autotuner and AI Advisor proposal suite") matches both call sites. | |

### Group M — Unified SPA shell (F38–F40)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F38 SPA 6-tab shell** | `templates/ai_advisor.html` (6 tabs, one server render); `static/ai_advisor.js` `initTabSwitcher` | Load `GET /ai-advisor` (`app.py:2848`) live; switch through all 6 tabs (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) in-place; `node --check static/ai_advisor.js`. | All 6 tabs render in one page; tab switching is in-place (no full reload); JS parses clean (`node --check` passes); eyes-on screenshot confirms each tab's panel renders (not blank/error). | CLAUDE.md `templates/ai_advisor.html` + `static/ai_advisor.js` rows (6 in-place tabs, `initTabSwitcher`) match. Confirm the deleted per-tab templates are NOT recreated (gotchas). | |
| **F39 GET redirects** | 5 GET sub-routes 302→`/ai-advisor` (`app.py:3023/3033/3174/3381/3794`) | `curl -sI` each of `/ai-advisor/correlations`, `/asset-swaps`, `/logic-changes`, `/chat`, `/strategy-builder` on :8090. | Each returns 302 with `Location: /ai-advisor`; the standalone per-tab pages no longer exist. | CLAUDE.md "all 5 GET sub-routes 302-redirect to /ai-advisor" + the Strategy-Builder-template-deleted gotcha match. | |
| **F40 CSRF on POSTs** | CSRF infra `_validate_csrf`/`_csrf_before_request`; POST routes `accept/reject/suggest/*-evaluate/chat-send/strategy-builder-run` | POST to a protected route WITHOUT a CSRF token (live, non-pytest) → expect rejection; WITH a token → accepted. Confirm none of these routes is in `_SETTINGS_WRITE_ALLOWLIST`. | A tokenless POST is rejected (403/CSRF error); a valid-token POST proceeds; all advisor action routes are advisory-only — none can write `LIVE_EXECUTION` or credential keys. | CLAUDE.md Architecture Constraint #2 (two guarded write paths; advisor routes NOT trade surfaces) + the strategy-builder route "not in allowlist" claim match. | |

---

## Edge Cases

- **C1 not merged before closeout:** AC-1 fails → closeout is BLOCKED. Do not verify the
  pipeline synthesis path against the stale hardcoded-Haiku code and call it done; the doc
  line ("Claude Haiku synthesis") would also stay wrong. Merge + deploy PR #39 first.
- **Off-hours / flat holdings (empty `logic_holdings`):** expected at 03:00 / weekends.
  The proxy floors (F6) must still yield a real universe; if a lens returns
  `available=False` purely because the universe was empty, that is a hollow-wiring
  regression, not honest degradation.
- **All lenses genuinely unavailable (keys missing / APIs down):** a `limited-inputs`
  verdict is acceptable ONLY then. A `limited-inputs` verdict while lenses HAVE data is a
  defect (degenerate synthesis) — loop back, do not sign off.
- **Stale FRED data (derivatives):** the freshness guard must fire (`reason="stale_data"`).
  If stale data is served as current, PR #37's fix has regressed — closeout FAIL.
- **Dead fundamentals lens:** if `_build_fundamentals_section(ticker=None)` returns
  `available=False` with a non-empty universe, PR #38's fan-out fix has regressed.
- **Duplicate MARKET_PRISM rows for one run_id:** a retry/double-write is a defect; the
  synthesizer checks for an existing row before writing (runbook constraint). Verify count==1.
- **Two writers race (pipeline vs council on the same night):** `[interpretation]` confirm
  the nightly authoritative writer post-Epic-A; ensure the 03:00 pipeline and an
  operator-driven council run do not both write a row for the same logical night without a
  clear precedence. Resolve during execution; document the decision.
- **Overview renders but is visually wrong:** a green render-poll + 0 console errors is
  necessary but NOT sufficient — the PM must read the screenshot. (Per the standing
  "actually LOOK at the render" rule.)
- **Doc says stub, code is live (or vice-versa):** any doc/behavior contradiction is a
  closeout FAIL filed against AC-10 and corrected before sign-off. The macro-stub mislabel
  is the canonical example to hunt for.
- **Analyst files a read but its message never reaches the synthesizer (inbox lag):** the
  synthesizer must derive availability from audit-DB rows, not its inbox (F17). If it marks
  a lens non-responsive that DID file an `initial_read`, that is a protocol regression.
- **Operator unavailable for sign-off:** Phase-4 stays hard-blocked. The PM does not
  unilaterally clear AC-11.

**Cluster 2 (AI Advisor suite) edge cases:**

- **HF-1 community-strats hollow in prod:** the Strategy Builder route runs template-only —
  community strategies are reachable only from tests. Do NOT mark F35 "pass" as if community
  candidates flow through the live UI. Adjudicate with the operator (AC-17): build the route
  injection (own cycle) or correct the "injected at the route boundary" doc claim. Verifying
  only the engine layer and calling the feature live is exactly the hollow-wiring trap.
- **CSRF blocks the live route checks:** the daemon enforces CSRF outside pytest, so the
  Cluster-2 POST-route probes MUST carry a valid token. A tokenless probe failing is the
  EXPECTED F40 behavior, not a verification failure — distinguish "CSRF correctly rejected"
  from "route broken."
- **No Composer key:** the Strategy Builder / asset-swap / logic-change backtests need a
  Composer key. Absent it, those engines return an honest no-key result — verify that is an
  informative degradation (D-1), not a 500. Do not call the feature "verified live" off the
  no-key path alone; the happy path needs a key present.
- **Empty suggestions on a strict symphony:** expected (F24/F27), not a failure — the
  assessment must explain `oos_alpha=None`. A blank panel with no explanation IS a failure.
- **Tab renders but is visually wrong:** a 200 + JSON shape is necessary but not sufficient
  for the visual tabs (Correlations, Strategy Builder cards) — eyes-on the screenshot
  (F28/F32/F38) before asserting correctness, same rule as AC-9.
- **`node --check` fails on `ai_advisor.js`:** a client-side parse error makes tab switching
  silently dead while every server/template test stays green. The static JS check is
  mandatory for F38.

## Security Considerations

- **Real API keys in use:** `FRED_API_KEY`, SEC UA string, Alpaca creds, `ANTHROPIC_API_KEY`
  (Opus 4.8). All must be present in the verification environment. No key is logged, echoed
  to the audit log, the Overview tab, Discord, or any closeout artifact.
- **D-1 contract everywhere:** every failure arm verified surfaces `type(exc).__name__`
  only. FRED embeds the API key in the request URL, so `str(exc)` on a macro/derivatives
  failure could leak the key — AC-7 explicitly checks the reason is a bare type name.
- **Warehouse secret-strip:** AC-4 confirms `_strip_secrets` removes
  `{api_key,token,secret,password,Authorization}` recursively from `raw_json` before persist.
- **Advisory-only / no execution path:** AC-12 — nothing in the closeout touches
  `LIVE_EXECUTION`, trade orders, or position state. The MARKET_PRISM row is
  `is_advisory_only=1` (DB-enforced). The Overview tab is read-only.
- **Prompt injection:** lens data from external APIs (GDELT/FRED/SEC) enters analyst
  prompts during the council run. Analysts treat all external data as untrusted text. No
  new mitigation is added at closeout beyond what Phase-2 established; the closeout confirms
  no analyst executes external content.
- **Opus spend:** the capstone council run incurs real multi-agent Opus 4.8 spend (operator-
  authorized). Bounded debate (≤3 rounds) and bounded clarification caps runaway spend; the
  operator note records total spend.

**Cluster 2 (AI Advisor suite) security:**
- **Advisory-only, DB-enforced:** every advisor-observation row (`ASSET_SWAP`,
  `LOGIC_CHANGE`, `MARKET_PRISM`, `ADD_CANDIDATE`) is `is_advisory_only=1` at the insert
  layer; AC-15 confirms no advisor route writes `LIVE_EXECUTION` or credential keys and none
  is in `_SETTINGS_WRITE_ALLOWLIST`.
- **CSRF on all POST action routes** (F40/AC-15): accept/reject/suggest/*-evaluate/chat-send/
  strategy-builder-run all require a token. The closeout verifies tokenless rejection live.
- **Chat explain-only boundary** (F31): `explain_artifact` must never reach a trade, a
  config write, OOS re-validation, `suggest_swaps`, or `run_backtest`; the artifact allowlist
  strips unknown fields (`validate_artifact`, re-validated defense-in-depth). Prompt-injection
  via artifact content is contained by the allowlist + the explain-only system prompt.
- **D-1 on every suite engine:** route errors surface `type(exc).__name__` only — Composer/
  Anthropic exceptions may embed keys; `app.py` logs `exc_info` server-side but returns only
  the class name (e.g. `:3449`, `:3819`).

## Testing Strategy

**No new automated tests** — this is an observed live verification, not a codepath. The
acceptance bar is the PM's direct observation of each matrix row on the real environment +
the operator's sign-off. Existing unit suites remain a necessary-but-not-sufficient
backstop (they did not catch the dead fundamentals lens, the stale-VIX serve, or the macro
mislabel — those needed live verification).

**Execution protocol:**
1. **Precondition (AC-1):** merge PR #39, deploy to the :8090 tree, confirm the deployed
   `lens_pipeline.py` reads `ADVISOR_SYNTHESIS_MODEL`. Confirm all keys present + Opus spend
   authorized. Verify HEAD SHA of the running tree.
2. **Group A probes (F1–F8):** for each lens, drive both the success arm and the failure
   arm via a read-only verifier agent (or PM) calling the real `_build_*_section()` on the
   deployed tree; record evidence in the matrix.
3. **Group B probes (F9–F14):** warehouse round-trip + secret-strip; CLI writer round-trip
   + D-1 error arm; migration-032 check; a live `run_pipeline()` non-dry-run; scheduler
   wiring confirmation.
4. **Group C / capstone (F15–F21):** drive the `prism-synthesizer` Agent Team once on real
   data per the runbook; collect the audit trail; verify one-row-per-run; render + eyes-on
   the Overview tab.
5. **Cluster-2 suite probes (F22–F40):** with the daemon live, load `GET /ai-advisor`;
   `node --check static/ai_advisor.js`; switch through all 6 tabs (eyes-on each panel);
   `curl -sI` the 5 GET redirects; drive each POST action route with a valid CSRF token +
   real engine call (suggest/accept/reject/asset-swaps-evaluate/logic-changes-evaluate/
   chat-send/strategy-builder-run); verify the safety boundaries (allowlist reject,
   tokenless-POST reject, chat explain-only) and the FDR/overfit invariants; verify the HF-1
   community gap directly (engine layer works; no route injection).
6. **Doc-accuracy sweep (AC-10 + AC-18):** for every feature in BOTH clusters, diff the doc
   claim against verified behavior; file + correct contradictions (the macro-stub mislabel
   class; the HF-1 "injected at the route boundary" claim; the C1 "Haiku synthesis" line).
   The doc-writer on the closeout team lands corrections before sign-off.
7. **HF-1 adjudication (AC-17):** present the community-strats production gap to the operator;
   decide build-the-injection (own cycle) vs deferred-by-design; correct the doc accordingly.
8. **Operator sign-off (AC-11):** surface artifacts; receive explicit go-ahead before
   Phase-4.

**Fixture provenance:** N/A — this is a live run on real APIs, not a fixture-backed test.
Evidence is captured-from-live (screenshots, audit-DB dumps, query outputs), not authored.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Closeout is a strict SUPERSET of `market-prism-phase3-observed-proof-run.md` | Phase-3 covers only the capstone run (AC-1..AC-5). The operator mandated "EVERY feature" — that requires the per-lens, warehouse, audit-foundation, pipeline, and C1 rows + per-feature doc checks that phase-3 omits. |
| Both execution layers (pipeline + council) verified separately | They are different writers of the MARKET_PRISM row; verifying only the council leaves the 03:00 nightly path (what actually runs blind) unproven. |
| Per-feature doc-accuracy is an AC, not a footnote | A doc claim contradicted by live behavior (macro "stub" mislabel) is a real defect class this closeout exists to catch. |
| Both arms (success + honest-degradation) verified per lens | The dead-fundamentals + stale-VIX defects were degradation-path failures; verifying only the happy path is what let them ship. |
| C1 (PR #39) merge+deploy is a hard precondition (AC-1) | The pipeline synthesis path and its doc line are wrong until C1 lands; verifying against stale hardcoded-Haiku code would bake in a doc/behavior mismatch. |
| Capstone council run is operator-gated; Phase-4 hard-blocked on sign-off | "Prove it before trusting it to run blind" — the operator must see the real artifacts before any unattended schedule. |
| Eyes-on screenshot read is mandatory for AC-9 | A render-poll + 0 console errors is necessary but not sufficient for visual correctness. |
| Cluster 2 (the suite) is capped by live-rendered-tab + real-engine-call evidence | Operator directive: the closeout covers the ENTIRE AI Advisor system, not just the council. Each tab/route must be exercised live, not just unit-green — unit suites missed the dead-fundamentals/stale-VIX/macro-mislabel defects. |
| HF-1 is a finding, not a pass; adjudicated with the operator (AC-17), not silently flipped | The community-strats feature is built + tested but has no production route caller. Verifying only the engine and calling it "live" is the hollow-wiring trap. The doc claiming route-boundary injection is contradicted by `app.py:3437`. |
| The closeout does NOT build the HF-1 route injection | Scope is verification, not development. Adding the injection (if adopted) is its own TDD cycle. The closeout adjudicates + documents the gap. |

## Scope Boundaries

- **IN:** live end-to-end verification of all **40 features** on the real environment —
  Cluster 1 (F1–F21: lenses both arms, warehouse, audit-foundation, pipeline, scheduler,
  the capstone observed multi-analyst council run) AND Cluster 2 (F22–F40: every SPA tab
  rendered live + every action route driving its real engine on the live DB, safety
  boundaries, FDR/overfit invariants); per-feature doc-accuracy reconciliation (corrections
  filed + landed, incl. HF-1 + the C1 doc line); the HF-1 hollow-wiring adjudication;
  operator sign-off gate; the C1 merge+deploy precondition check.
- **OUT:** building any new code or new tests (this is verification, not development) —
  including the HF-1 community-route injection itself (that, if adopted, is its own TDD
  cycle; the closeout only adjudicates + documents the gap); enabling Phase-4 unattended
  scheduling (hard-blocked until AC-11 sign-off — Phase-4 is its own feature,
  `market-prism-phase4-unattended-scheduling.md`); any change to producers, engines, agents,
  or templates beyond doc corrections; reprocessing historical runs; Epic-B lens quality
  enrichment beyond what is already shipped.

**Dependencies:**
1. **C1 / PR #39** (`feat/advisor-synthesis-model-config`) merged to origin AND deployed to
   the running :8090 tree — gates AC-1 and the model-literal reconciliation across BOTH
   clusters: F13/F20 (pipeline), F23 (config advisor, `ai_advisor.py:59`), and F31 (chat,
   `advisor_chat.py:211`). PR #39 touches all three modules + their docs.
2. The :8090 daemon running the **deployed** post-PR-#39 code (not a stale tree).
3. Keys present: `FRED_API_KEY`, `ANTHROPIC_API_KEY`, Alpaca creds, SEC UA, **Composer key**
   (for the Strategy Builder / asset-swap / logic-change backtests); Opus 4.8 spend
   authorized.
4. Phases 1 + 2 (audit-log foundation + council agents) + the full AI Advisor suite merged
   and clean on main (already on `348dc26`).
5. ≥2 live symphonies with return series for the Correlations tab (F28); at least one live
   symphony for the Config Advisor / swap / logic-change checks.
6. Valid CSRF tokens obtainable for the live POST-route checks (the daemon enforces CSRF
   outside pytest).
7. Operator available for the AC-11 sign-off gate AND the AC-17 HF-1 adjudication.

**Closeout team composition (when executed):** a non-TDD verification Agent Team —
read-only verifier(s) driving the Cluster-1 Group A/B probes AND the Cluster-2 tab/route
probes (flask-dashboard-specialist for the live-render + CSRF route checks), the
`prism-synthesizer` + 5 analysts for the capstone council run, a `doc-gen` doc-writer
landing the AC-10/AC-18 corrections (incl. HF-1 + C1 doc lines), and a synthesizing lead
reconciling the two-cluster matrix into one verdict. No Toxic Pair (no code written).
