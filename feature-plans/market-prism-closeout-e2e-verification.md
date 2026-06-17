# Feature: AI Advisor System Closeout — Full End-to-End Verification
Status: ready
Created: 2026-06-17
Updated: 2026-06-17 (scope expanded from Market Prism council to the ENTIRE AI Advisor system — operator directive)
Refreshed: 2026-06-17 by `closeout-synth` — re-validated every anchor against worktree
`73dc603` (=origin/main, post-C1). The matrix was authored vs `348dc26` (pre-C1).

> **REFRESH BANNER (@ 73dc603, by closeout-synth).** Every file:line cite below was
> independently re-validated against the worktree's current code. Drift found + corrected:
> 1. **C1 MERGED (was OPEN PR #39, now `73dc603` / PR #41).** AC-1, HF-2, F20, the inventory
>    anchors, and the F20 matrix row are all refreshed: `lens_pipeline.py:285` reads
>    `ADVISOR_SYNTHESIS_MODEL` (default `claude-opus-4-8`); accessor
>    `ai_advisor.resolve_advisor_model()` (`:63-69`); no Haiku literal on the synthesis path.
>    **Daemon deploy ⏳ PENDING** (running :8090 PID 5752 holds pre-C1 code; PM deploys
>    post-close ~14:00 MDT). F13/F20 live-against-deployed-daemon checks run AFTER that.
> 2. **All 5 lens builders shifted ~+7 lines** (C1 added `resolve_advisor_model` at the top
>    of `ai_advisor.py`): technicals `:463`, sentiment `:537`, derivatives `:658`, macro
>    `:739`, fundamentals `:1083`. Cited positions corrected throughout.
> 3. **Engine-layer anchors corrected:** `propose_strategies` def `:855`, `validate_artifact:168`,
>    `explain_artifact:334`. Cluster-2 `app.py` routes + engine call sites are UNCHANGED
>    (GET `/ai-advisor:2848`; POST `:3042/3183/3394/3619/3686/3762/3803`; redirects
>    `:3023/3033/3174/3381/3794`).
> 4. **HF-1 RE-CONFIRMED hollow @ 73dc603** (`app.py:3437` passes no `community_candidates`;
>    `grep -c` community wiring in `app.py` = 0). Finding stands.
> 5. **DECISIONS.md is internally contradictory on community-strats:** `:633` ("no
>    production caller yet" — TRUE) vs `:651` ("the caller owns the Atlas fetch and passes
>    the adapted output" — implies a caller that does not exist). Both + the CLAUDE.md
>    "injected at the route boundary" row must be reconciled to verified state (AC-17/AC-18).
>
> **LIVE-ENVIRONMENT CONSTRAINTS (binding on all auditors — AC-12 market-hours-safe):**
> - **Market is OPEN** (verified 08:08 MDT; RTH = 07:30-14:00 MDT). The live engine runs a
>   1-min cadence. **NO second `app.py` daemon/engine may be started.** Use the Flask
>   **test client**, direct producer/engine calls, and **read-only** SQLite ONLY. Nothing
>   may touch `LIVE_EXECUTION` or WRITE the state DB (advisory-only rows are the sole writes
>   the suite normally makes — and even those should be avoided in the audit; prefer
>   read-only inspection of existing rows + dry-runs).
> - **External-API rate limits:** Composer 1 req/s, GDELT 1 req/5s, SEC EDGAR pacing,
>   Anthropic Opus spend. Issue **at most a single bounded probe per external lens**; prefer
>   fixtures / cached data / existing DB rows where a live call would hammer.
> - **Evidence bar:** every PASS/FAIL needs a `file:line` AND a runnable result (test-client
>   render / direct call / read-only SQL / static cite). A finding with neither is
>   DOWN-RANKED in synthesis.

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
- **HF-2 (stale doc): C1 not merged — RESOLVED @ 73dc603.** PR #41 (the C1 work) merged at
  `73dc603`; `lens_pipeline.py:285` now reads `ADVISOR_SYNTHESIS_MODEL` (default
  `claude-opus-4-8`) and the CLAUDE.md `lens_pipeline` row already documents the configurable
  model (no surviving "Claude Haiku synthesis" wording in CLAUDE.md). The only residual is
  the **deploy** of the running daemon (PM does post-close) — see refreshed AC-1/F20. The
  doc-writer must still confirm no stale "Haiku" wording survives anywhere in
  `docs/generated/` or DECISIONS.md (AC-10).

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
| F20 | Model config | C1 `ADVISOR_SYNTHESIS_MODEL` env var (default Opus 4.8) — **MERGED @ 73dc603 (PR #41); daemon deploy ⏳ pending (PM post-close)** |
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
- 5 lens builders (REFRESHED @ 73dc603 — all shifted ~+7 lines by the C1 `resolve_advisor_model` addition): `ai_advisor.py:463` (technicals), `:537` (sentiment), `:658` (derivatives), `:739` (macro), `:1083` (fundamentals; `_fetch_fundamentals_for_ticker:938`).
- 5-lens set + `_call_lens_section`: `advisors/lens_pipeline.py:55` (`_call_lens_section`), `:99` (`_collect_lenses`), `:129` (`_validate_and_filter_sources`).
- Freshness guard: `advisors/lens_options_proxy.py:313` (`_fetch_options_proxy`), `:366` (`reason="stale_data"`).
- Audit accessors: `database.py:1217` (`insert_prism_audit_entry`), `:1252` (`get_prism_audit_for_run`), `:1180` (`get_latest_market_prism_summary`), `:1053` (`insert_advisor_observation`).
- Warehouse: `advisors/lens_warehouse.py:110/127/181`; sentiment+macro wiring `ai_advisor.py:585-630` / `:757-855`.
- CLI writer: `advisors/prism_audit_write.py` (whole file).
- Overview prefetch: `app.py:2948-3019`; render block `templates/ai_advisor.html:942-976`.
- 6 council agents: `.claude/agents/prism-synthesizer.md` + 5 `prism-*-analyst.md` (all `model: opus`).
- C1 MERGED @ 73dc603: `advisors/lens_pipeline.py:285` reads `ADVISOR_SYNTHESIS_MODEL` (default `claude-opus-4-8`); accessor `ai_advisor.resolve_advisor_model()` (`:63-69`); 2nd call site `ai_advisor.py:1639`. No `claude-haiku-4-5-20251001` literal on the synthesis path. Daemon deploy ⏳ pending (PM post-close).

**Cluster 2 — AI Advisor suite anchors:**
- Routes: `GET /ai-advisor` (`app.py:2848`); GET redirects `/correlations:3023`, `/asset-swaps:3033`, `/logic-changes:3174`, `/chat:3794`, `/strategy-builder:3381`; POST `/asset-swaps/evaluate:3042`, `/logic-changes/evaluate:3183`, `/strategy-builder/run:3394`, `/suggest:3619`, `/accept:3686`, `/reject:3762`, `/chat/send:3803`.
- Engine call sites (live consumers — none hollow except F35): `compute_pairwise_correlations` ← `app.py:2910`; `propose_operator_swap` ← `:3114`; `propose_operator_logic_change` ← `:3264`; `propose_strategies` ← `:3437`; `explain_artifact` ← `:3888`.
- Allowlist: `_SUGGESTIBLE_ALLOWLIST` = 6 Optuna keys ∪ `MAX_SQUEEZE_FLOOR` (`ai_advisor.py:1718`); `enforce_suggestion_allowlist:1743`; `_UNTUNED_SUGGESTIBLE_KEY="MAX_SQUEEZE_FLOOR":73`.
- Asset swap: `LENS_BLEND_WEIGHT=0.25` (`asset_swap_engine.py:78`), `_apply_lens_blend:372`.
- Strategy builder (REFRESHED @ 73dc603): `propose_strategies` def `:855`, `community_candidates` kwarg `:864`, applied `:921-922`; `community_candidate_infos:195`; `MAX_COMMUNITY_CANDIDATES_PER_RUN=20`; gate `backtest_gate_engine.evaluate_candidate_batch` (BHY/Yekutieli FDR); rate limit `composer_backtest_client.py` (1 req/s).
- Chat (REFRESHED @ 73dc603): `validate_artifact:168`, `explain_artifact:334` (def line; called at `app.py:3888`); `CHAT_ARTIFACT_ALLOWED_FIELDS` near top of `advisor_chat.py`.
- Community: `load_community_strategies:98` (atlas_cache `cached_pull:156`, structural-hash `_composition_hash:63`).
- **HF-1 (hollow) — RE-CONFIRMED @ 73dc603:** `app.py:3437` calls `propose_strategies(objective=, universe=, screen_config=ScreenConfig(), live_returns=[], symphony_id=)` with NO `community_candidates=` argument (verified by reading `app.py:3437-3443`). `grep -c "community_candidates\|load_community_strategies\|community_candidate_infos"` in `app.py` = **0**. The engine layer is fully built (`strategy_builder_engine.community_candidate_infos:195`, `propose_strategies` `community_candidates` kwarg `:864` applied `:921-922`, `community_strats.load_community_strategies:98`) but unreachable from any production route. STILL HOLLOW — finding stands.

---

## Acceptance Criteria

Each AC is a closeout gate. The closeout PASSES only when **every** AC passes; any FAIL
loops back to the owning feature's cycle (do not paper over a degenerate result).

- [x] **AC-1 (Dependency precondition) — REFRESHED @ 73dc603 → PASS (code) / DEPLOY ⏳ pending (W2):** C1 (PR #41, merged as
  `73dc603`) IS NOW MERGED to origin ✅. `ADVISOR_SYNTHESIS_MODEL` (default
  `claude-opus-4-8`) governs the synthesis path: `advisors/lens_pipeline.py:285`
  (`model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`) +
  `ai_advisor.resolve_advisor_model()` accessor (`ai_advisor.py:63-69`) + a 2nd env-read
  call site (`ai_advisor.py:1639`). The old `claude-haiku-4-5-20251001` literal is GONE
  from the synthesis path (`grep` → 0 hits in `lens_pipeline.py`/`ai_advisor.py`). **Deploy
  ⏳ PENDING:** the running :8090 daemon (PID 5752, confirmed LISTENING) still holds pre-C1
  code in memory — the PM deploys post-close (~14:00 MDT today). Verified by reading the
  worktree's `lens_pipeline.py:285` on `73dc603` + a config probe. **Refresh note:** the
  matrix was authored vs `348dc26` (pre-C1, hardcoded Haiku at `:284`); HF-2 (stale-doc:
  C1 not merged) is now **RESOLVED at code level** — only the deploy + the F13/F20
  live-against-deployed-daemon checks remain.
  **[interpretation]** `lens_pipeline.py:285` reads `os.environ.get(...)` directly rather
  than calling the shared `ai_advisor.resolve_advisor_model()` accessor — two env-reads of
  the same key duplicate the default literal. Not a defect (both default to
  `claude-opus-4-8`), but a minor cohesion finding to note in the verdict (down-ranked,
  not a closeout blocker).
- [x] **AC-2 (Per-lens live E2E) → PASS:** (F1–F5 all live `available=True` both arms; cluster1-groupA.md). For EACH of the 5 lenses (F1–F5), a live call through
  the real producer reaches `available=True` with **real, non-stub values** when its data
  source is reachable AND keys are present; AND the honest-degradation path returns
  `available=False` with a `type(exc).__name__`-only / informative reason when the source
  is unreachable or a key is absent. Both arms observed (not inferred).
- [x] **AC-3 (Universe floors) → PASS:** (F6 live `logic_holdings={}` → technicals `breadth=0.8` + fundamentals 6 tickers over proxy floors). With live `logic_holdings` empty (off-hours / flat), the
  technicals and fundamentals lenses still receive a non-empty universe from their proxy
  floors (F6), so breadth/fan-out are computed on a real basket — not hollow `available=False`.
- [x] **AC-4 (Warehouse persistence) → PASS:** (F9/F10 temp-DB round-trip + real warehouse rows from live calls; recursive secret-strip; pytest sentinel). After a live sentiment and macro fetch, new rows
  appear in the third DB (`alphabot_warehouse.db`) via `get_lens_snapshots`; secrets are
  stripped from `raw_json`; the pytest sentinel blocks opening the real warehouse under
  pytest (F9, F10).
- [x] **AC-5 (Audit-log foundation) → PASS:** (F11/F12 CLI round-trip prints positive row id; migration 032 last wired; D-1 error arm). The CLI writer (F12) writes a row and prints its id;
  `get_prism_audit_for_run` returns it; migration 032 is the last wired migration (F11).
- [~] **AC-6 (Nightly pipeline non-hollow) → DEFERRED (W2):** dry-run shape PASS (lenses_attempted=5/available=5) + scheduler wired (F14); the non-dry-run live MARKET_PRISM write needs the deployed daemon (PM post-close). NOT a Fail. A live `run_pipeline()` (non-dry-run) writes
  exactly one `MARKET_PRISM` row, runs all 5 lenses with per-lens isolation, validates
  citations, and synthesizes; when lenses have real data the verdict is a real sentiment,
  NOT `limited-inputs` (F13). The 03:00 scheduler is wired and would fire (F14).
- [~] **AC-7 (Council deliberation live) → DEFERRED (W3, operator-gated):** agent files verified (5 analysts + synthesizer, `model: opus`); the live multi-analyst run incurs real Opus spend + needs operator observation. NOT a Fail. The multi-analyst council (F15–F17) runs on
  real data under observation: all 5 analysts file `initial_read` rows; clarifying Q&A
  occurs where relevant; debate fires ONLY on genuine disagreement (≤3 rounds) and is
  ABSENT when analysts converge; the synthesizer derives availability from audit rows, not
  its inbox.
- [~] **AC-8 (One row per run) → DEFERRED (W3):** mechanism verified (`is_advisory_only=1` hardcoded `database.py:1069–1091`); the exactly-one-row count needs a real capstone `run_id` (F21). NOT a Fail. Exactly one `MARKET_PRISM` row exists for the capstone
  `run_id` (F18). A retry/duplicate is a defect to fix before sign-off.
- [x] **AC-9 (Overview renders, eyes-on) → PASS-WITH-FINDING (RF-1):** live :8090 eyes-on (render-gate.md) — sentiment chip semantic class, rationale prose, 5 lenses available, real sources, 0 console errors, zero raw-color leakage all PASS. **FINDING RF-1:** the 5 lens-card bodies render raw `json.dumps(payload)` not a human digest (`lens_pipeline.py:166–167` + `templates/ai_advisor.html:994–995`) — content-readability fail, follow-on fix cycle. The Overview tab renders the produced report on
  the live :8090 page; the PM Reads the screenshot **with its own eyes** and describes the
  sentiment chip, rationale, per-lens digest, and cited sources BEFORE asserting
  correctness; the informative empty-state renders when no row exists (F19).
- [x] **AC-10 (Doc-accuracy sweep) → PASS (after closeout-doc correction):** one doc FAIL found — F4-DOC-1 macro "stub" mislabel in `docs/generated/ai_advisor.md` + `INDEX.md`; corrected by closeout-doc this cycle. All other F1–F20 doc rows match verified behavior. For EVERY feature F1–F20, the feature's documentation
  (`docs/generated/`, the CLAUDE.md key-files row, DECISIONS.md) matches the verified live
  behavior. Any contradicted claim (e.g. a "stub" label on a live producer) is filed and
  corrected as part of closeout — a doc/behavior mismatch is a closeout FAIL.
- [~] **AC-11 (Operator sign-off gate) → DEFERRED (W3, operator-gated):** depends on the AC-7 capstone run; Phase-4 stays hard-blocked until the operator signs off. Synth does NOT clear this. The PM surfaces the capstone artifacts (rendered
  Overview screenshot + full audit-trail dump + lens-coverage/debate/spend note) to the
  operator and receives explicit sign-off. Phase-4 unattended scheduling stays
  **hard-blocked** until sign-off is received.
- [x] **AC-12 (No execution-path contamination) → PASS:** all probes used temp DBs / dry-run / read-only SQL; state DB received zero writes; warehouse rows are genuine lens snapshots (same as the 03:00 nightly would write); `is_advisory_only=1` DB-enforced (cluster1-groupB.md AC-12 confirmation). No closeout step touches
  `LIVE_EXECUTION`, trade orders, or position state. Every verified surface is advisory-only
  (`is_advisory_only=1` on the MARKET_PRISM and all advisor-observation rows).

**Cluster 2 — AI Advisor suite ACs:**

- [x] **AC-13 (Every tab renders live) → PASS:** (render-gate.md: all 6 tabs render live on :8090 w/ in-place switching, 0 console errors, zero raw-color leakage, design-token compliance; `node --check ai_advisor.js` EXIT 0; 5 GET sub-routes 302). All 6 SPA tabs (Overview, Correlations, Asset
  Swaps, Logic Changes, Chat, Strategy Builder) render on the live `GET /ai-advisor` page;
  tab switching is in-place; `static/ai_advisor.js` passes `node --check`; each panel is
  confirmed by an eyes-on screenshot read (F28/F38). The 5 GET sub-routes 302-redirect to
  `/ai-advisor` (F39).
- [~] **AC-14 (Every action route drives its real engine live) → PASS (route chains) / DEFERRED (live POSTs, W2):** all route→engine chains verified static + test-client shape (suggest/accept/reject/asset-swaps/logic-changes/chat/strategy-builder); the live POSTs needing a real Composer key + deployed daemon are DEFERRED to W2 (market-hours + no-key). NOT a Fail. Each POST action route
  invokes its real engine on the live DB with a valid CSRF token and returns the expected
  shape: suggest→suggestions, accept→3-gate apply, reject→no-write, asset-swaps/evaluate→
  gated swap candidates + persisted `ASSET_SWAP` row, logic-changes/evaluate→gated logic
  tweaks + persisted `LOGIC_CHANGE` row, chat/send→explanation, strategy-builder/run→
  gated survivor/rejected/FDR JSON + persisted survivors (F23/F26/F29/F30/F31/F32).
- [x] **AC-15 (Safety boundaries hold live) → PASS:** allowlist rejects out-of-scope keys live (F25/F26 test-client); C2 Gate-1 blocks before write; chat explain-only (grep 0 write/trade paths, F31); tokenless POST → 403 (F40); no advisor route in `_SETTINGS_WRITE_ALLOWLIST`, `LIVE_EXECUTION` absent. the 7-item allowlist rejects out-of-scope
  keys (F25); the C2 accept gates block on failure (F26); the chat is explain-only — no
  trade/no-write/no OOS-revalidation/no-backtest (F31); CSRF rejects tokenless POSTs (F40);
  NO advisor route is in `_SETTINGS_WRITE_ALLOWLIST` and none touches `LIVE_EXECUTION`.
- [x] **AC-16 (FDR / overfit invariants hold live) → PASS:** (F36 gate N=full batch, screens post-gate only; F37 same `acceptance_gate` module for autotuner + advisor suite — runnable module-id match). `[interpretation]` F34 1 req/s pacing is IMPLICIT (latency-dependent, ASSUMPTION-K-1, MED). the BHY/Yekutieli FDR gate runs
  across the FULL candidate batch (template + any community together — anti-overfit
  invariant), screens never shrink the gate input, and the same `acceptance_gate` governs
  autotuner + advisor suite (F36/F37). The Composer backtest client paces at ≤1 req/s (F34).
- [~] **AC-17 (Hollow-producer finding resolved — HF-1) → DOC RECONCILED (this cycle) / BUILD-VS-DEFER DEFERRED (W3, operator-gated):** HF-1 confirmed hollow (`app.py:3437` no `community_candidates=`, grep=0); the doc contradiction (CLAUDE.md "injected at the route boundary") is corrected by closeout-doc to "no production route caller." The build-vs-defer adjudication is the operator's call (PM notes operator leans BUILD on a separate cycle). NOT silently passed. the community-strategies
  production gap is explicitly adjudicated with the operator: EITHER route-level injection
  is added (then F35 is a build gap to fix on its own cycle) OR it is deferred-by-design
  (then the CLAUDE.md "injected at the route boundary" claim is corrected to match
  reality). The closeout does NOT silently pass F35 as if community strats were live in
  prod. This AC is satisfied by the adjudication + the doc correction, not by a green test.
- [x] **AC-18 (Suite doc-accuracy) → PASS (after closeout-doc correction):** two doc items found — (1) HF-1 community_strats "injected at the route boundary" FALSE → corrected to "not yet wired"; (2) C2-gates wording ("gates" implies all-block; code has 4 gates, Gate-2 logs-only) → reconciled. Both landed by closeout-doc this cycle. All other F22–F40 doc rows match. for every Cluster-2 feature F22–F40, the docs
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
| **F1 Technicals** | `advisors/lens_technicals.py` `_fetch_technicals` / `_PROXY_UNIVERSE`; `ai_advisor._build_technicals_section` (`:456`) | On the deployed tree, call `ai_advisor._build_technicals_section()` against live Alpaca; then force the failure arm (break Alpaca creds / empty universe). | `available=True` with real `ma_posture`, `breadth` (0..1), `momentum` over the proxy basket; AND failure arm returns `available=False` + `reason` (no `str(exc)`), no traceback. | CLAUDE.md `lens_technicals` row + DECISIONS lens-technicals entry match: 50/200 SMA, breadth excludes insufficient-history tickers, 20-day momentum, proxy floor `_PROXY_UNIVERSE`. Flag any "stub" wording. | **PASS** (cluster1-groupA.md F1: live `available=True`, `breadth=0.8`, `ma_posture`+`momentum` over proxy; failure arm D-1 `ai_advisor.py:513–534`) |
| **F2 Sentiment/GDELT** | `advisors/lens_gdelt.py` `_fetch_gdelt_sentiment` + artlist; `ai_advisor._build_sentiment_section` (`:530`) | Call `_build_sentiment_section()` live (GDELT key-less); observe tone + artlist citations; force artlist-only and tone-only arms (per-source isolation). | `available=True` with a numeric tone and ≥1 validated source; per-source isolation proven (one signal can fail, the other succeeds); `reason` is type-name only on full failure. | `.claude/gdelt-contract.md` + CLAUDE.md sentiment wiring match live shape (timelinetone endpoint, normalization). Confirm GDELT key-less claim. | **PASS** (cluster1-groupA.md F2: live `available=True`, `tone_score=0.003` tone-only arm; per-source isolation `ai_advisor.py:586–608`; D-1 `:560–566`/`:581–583`. `[interpretation]` `tone_summary` hardcoded `None` `:651` — non-blocking reserved field) |
| **F3 Derivatives + freshness guard** | `advisors/lens_options_proxy.py` `_fetch_options_proxy` (`:313`), freshness guard (`:347-366`); `ai_advisor._build_derivatives_section` (`:651`) | Call `_build_derivatives_section()` live with `FRED_API_KEY` set; THEN reproduce the stale-data arm (point at an old observation / simulate >threshold age) and confirm `reason="stale_data"`, `available=False`. | Fresh path: `available=True` with `vix_level`, `vix_term_structure.regime`, `risk_read`, `as_of_date` recent; stale path: `available=False` `reason="stale_data"` — stale FRED data NOT served as current (the PR #37 fix). | CLAUDE.md derivatives row + DECISIONS/PR-#37 entry describe the freshness guard accurately (threshold, `stale_data` reason). | **PASS** (cluster1-groupA.md F3: live `available=True`, `vix_level=16.41`, `as_of_date=2026-06-16` 1d<10d threshold; stale guard `lens_options_proxy.py:347–368` `reason="stale_data"` — PR #37 fix confirmed in code) |
| **F4 Macro** | FRED series fetch `ai_advisor._build_macro_section` (`:732`); `_FRED_SERIES` | Call `_build_macro_section()` live with `FRED_API_KEY`; then unset the key for the degradation arm. | `available=True` with ≥1 FRED series (10y/UNRATE/CPI/FedFunds) carrying value+date + clickable source per series; key-absent arm → `available=False` with the informative "register free at fred.stlouisfed.org" reason. **NOT a stub.** | **HIGH-RISK doc check (the "macro stub" mislabel class):** confirm no doc anywhere calls macro a stub/placeholder — it is a live FRED producer (`:780-869`). File + correct any stale "stub" wording. | **PASS (live)** / **FAIL (doc)** (cluster1-groupA.md F4: live `available=True`, 4 FRED series w/ value+date+source. **FINDING F4-DOC-1 — AC-10 doc FAIL:** `docs/generated/ai_advisor.md` + `INDEX.md` still say "macro stub". Corrected by closeout-doc this cycle) |
| **F5 Fundamentals fan-out** | `ai_advisor._fetch_fundamentals_for_ticker` + `_build_fundamentals_section` (`:1076`), `_FUNDAMENTALS_PROXY_UNIVERSE` | Call `_build_fundamentals_section()` (ticker=None, portfolio path) live against SEC EDGAR; verify fan-out over holdings∪proxy; confirm single-ticker path (`ticker="AAPL"`) byte-preserved; force all-fail arm. | Portfolio path: `available=True` with `per_ticker_results` for ≥1 company ticker + deduped SEC sources; per-ticker honest degradation (one ticker fails, others resolve); all-fail → `available=False`. Single-ticker path unchanged (PR #38 fix — dead lens now live). | CLAUDE.md fundamentals row (DE-FUND-001) matches: 8 large-cap COMPANY tickers (not ETFs), bounded fan-out, no invented composite ratios. | **PASS-WITH-FINDING** (cluster1-groupA.md F5: live portfolio `available=True`, 6/8 tickers, honest per-ticker degradation; single-ticker AAPL byte-preserved; all-fail `:1185–1192`. **FINDING (cluster1-F5-vintage.md): TWO CONFIRMED vintage defects** — Mode A deprecated-tag `ai_advisor.py:354–360` + Mode B sort-by-`filed` `ai_advisor.py:1008–1019` → wrong-vintage values. NOT a closeout blocker (lens available w/ real data); follow-on fix cycle) |
| **F6 Universe floors** | `lens_technicals._PROXY_UNIVERSE`; `ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE` | With live `logic_holdings` empty (verify via `database.load_state()`), confirm both lenses still build a real universe (proxy floor applied). | Technicals breadth + fundamentals fan-out computed on the proxy basket despite empty holdings — no hollow `available=False` from an empty universe (DE-TECH-002). | CLAUDE.md describes both proxy floors and the off-hours rationale; confirm the floor tickers listed match code. | **PASS** (cluster1-groupA.md F6: live `logic_holdings={}`; technicals `breadth=0.8` + fundamentals 6 tickers both produced over proxy floors; unconditional union `ai_advisor.py:508`+`:1152`; floor tickers match code — DE-TECH-002 works) |
| **F7 Honest-availability contract** | All 5 builders; `lens_pipeline._call_lens_section` (`:54`) D-1 wrapper | Across F1–F5 failure arms, assert the fixed 5-key contract `{lens, available, reason, payload, sources}` and that `reason` is only `type(exc).__name__` (no message/URL/key leak — FRED embeds the key in the URL). | Every failure arm: 5-key dict, `payload=None`, `sources=[]`, `reason` a bare type name or curated string — no `str(exc)`, no API key, no traceback. | DECISIONS DE-ML-001 (honest-availability contract) matches live behavior. | **PASS** (cluster1-groupA.md F7: all 5 builders + `_call_lens_section` `lens_pipeline.py:86–96` use `type(exc).__name__` only; no `str(exc)`; FRED key-leak risk explicitly avoided `ai_advisor.py:749–750`) |
| **F8 Citation validation** | `ai_advisor.build_citation`; `lens_pipeline._validate_and_filter_sources` (`:128`) | Feed a malformed citation through a live lens; confirm it is dropped, not surfaced. | Only well-formed `{title,url,published,lens}` sources appear in the Overview "cited sources"; malformed entries silently dropped. | CLAUDE.md / DECISIONS citation convention (DE-ML-002, `raw_response` JSON) matches. | **PASS** (cluster1-groupA.md F8: `build_citation` `ai_advisor.py:1230–1252` filter; `_validate_and_filter_sources` `lens_pipeline.py:140–148` drops malformed; live sources all well-formed) |

### Group B — Warehouse, audit-log foundation, nightly pipeline (F9–F14)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F9 Lens Data Warehouse** | `advisors/lens_warehouse.py` `init_warehouse_db` (`:110`), `persist_lens_snapshot` (`:127`), `get_lens_snapshots` (`:181`), `_strip_secrets` | `init_warehouse_db()` on the live `alphabot_warehouse.db`; `persist_lens_snapshot` with a payload containing a fake `api_key`; read it back via `get_lens_snapshots`; confirm the pytest sentinel raises when opening the real warehouse under pytest. | Row round-trips; `raw_json` has the secret key stripped (recursive `_strip_secrets`); append-only; WAL; sentinel blocks pytest from the real file. **Third DB confirmed distinct** from state + optimization (no cross-DB join). | CLAUDE.md `lens_warehouse` row (DW-1) matches: third DB, append-only, secret-strip set, engine-agnostic `raw_json`. | **PASS** (cluster1-groupB.md F9: temp-DB round-trip, WAL confirmed, recursive secret-strip `api_key`+nested `token` removed `lens_warehouse.py:70–79`; pytest sentinel `:52–58`; third DB distinct from state) |
| **F10 Warehouse wiring** | `ai_advisor._build_sentiment_section` (`:585-630`) + `_build_macro_section` (`:757-855`) persist calls | After live F2 + F4 calls, query `get_lens_snapshots("sentiment")` and `("macro")`; confirm new rows for this run. | Sentiment (GDELT) and macro (FRED) each wrote a snapshot row (available True AND False paths both persist); off-execution-path lazy import (no module-level warehouse import). | CLAUDE.md states sentiment+macro are wired "non-hollow"; confirm no OTHER lens claims warehouse wiring it doesn't have. | **PASS** (cluster1-groupB.md F10: real warehouse has 4 macro + 1 sentiment rows from Group A live calls; separate from state DB; raw_json zero secrets; persist cites `ai_advisor.py:633–645`/`:858–870` lazy-import + D-1) |
| **F11 Migration 032 + accessors** | `database.py` migration 032, `insert_prism_audit_entry` (`:1217`), `get_prism_audit_for_run` (`:1252`) | `python -c "import database; print(database._MIGRATION_FILES[-1])"` → `032_prism_audit_log.sql`; insert + read-back a row on the live DB. | Last wired migration is 032; round-trip returns the row keyed by `run_id`, ordered append-only. | CLAUDE.md database row lists migration 032 + both accessors with correct signatures. | **PASS** (cluster1-groupB.md F11: `_MIGRATION_FILES[-1]='032_prism_audit_log.sql'` (29 wired); temp-DB round-trip returns all 4 fields; parameterized `database.py:1217–1249`; `is_advisory_only=1` hardcoded `:1069–1091`) |
| **F12 CLI writer** | `advisors/prism_audit_write.py` (whole file) | `echo "test read" \| python -m advisors.prism_audit_write --run-id <rid> --role technicals_analyst --phase initial_read` with `DB_PATH` set to live DB; confirm it prints a positive row id; confirm a missing-arg / bad invocation prints only a type name (D-1) and exits non-zero. | STDOUT = positive integer row id; the row is readable via `get_prism_audit_for_run`; error arm leaks no traceback/message. | CLAUDE.md `prism_audit_write` row matches the `--run-id/--role/--phase` + STDIN contract. | **PASS** (cluster1-groupB.md F12: subprocess STDOUT='1' exit 0; error arm STDOUT empty exit 2 no traceback; D-1 `type(exc).__name__` `prism_audit_write.py:42–82`; no Flask dep) |
| **F13 Nightly pipeline 4-pass** | `advisors/lens_pipeline.run_pipeline` (`:314`), `_collect_lenses` (`:98`), `_synthesize_via_claude` (`:243`) | Run `run_pipeline()` (non-dry-run) live with all keys present; inspect the returned dict + the written `MARKET_PRISM` row. | Exactly one MARKET_PRISM row written; all 5 lenses attempted with per-lens isolation (`lenses_attempted=5`); citations validated; a REAL `verdict` (not `limited-inputs` when lenses have data); `available_lens_count` reflects reality. | CLAUDE.md `lens_pipeline` row + DECISIONS DE-CY4-001 match; **confirm the synthesis-model line reflects C1 (env var), not the old hardcoded Haiku** (AC-1 dependency). | **PASS (Wave 1) / DEFERRED (Wave 2)** (cluster1-groupB.md F13: dry-run `lenses_attempted=5`, `lenses_available=5`, `market_prism_row_id=None`; env-var `lens_pipeline.py:285` default `claude-opus-4-8`, zero Haiku literals. **Non-dry-run live write DEFERRED to W2** — needs deployed daemon, PM post-close ~14:00 MDT) |
| **F14 03:00 scheduler** | `app.py` `run_scheduler` (`:437-443`) → `_run_lens_pipeline` (`:425`) → daemon thread → `_lens_pipeline_worker` (`:410`) → `run_pipeline` lazy import (`:417`) | Confirm the daemon registers the 03:00 Prism job (`schedule.every().day.at("03:00").do(_run_lens_pipeline)`, `app.py:443`) on the running tree; check daemon log for the scheduled entry. | The 03:00 job is registered and invokes `run_pipeline` via a daemon thread + lazy import (never module-level — CC-2), so the scheduler thread returns immediately. | CLAUDE.md states 03:00 daily via `run_scheduler()`; confirm time (`03:00`, off-hours, no overlap with `:00` execution path) + lazy-import boundary documented accurately. | **PASS** (cluster1-groupB.md F14: `app.py:443` `schedule.every().day.at("03:00").do(_run_lens_pipeline)`; CC-2 lazy import `:417`; daemon thread `:433` — scheduler never blocks the 1-min path) |

### Group C — Council agents, persistence, Overview, model config (F15–F20)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F15 5 analyst agents** | `.claude/agents/prism-{technicals,sentiment,derivatives,macro,fundamentals}-analyst.md` | During the capstone run (F21), each analyst pulls its lens via `_call_lens_section`, forms a real read, and files `phase=initial_read` to the audit log. | 5 distinct `initial_read` audit rows (one per analyst role), each reflecting its real lens data; model=opus confirmed in each agent file. | Each analyst file's described lens + role string matches its audit `agent_role`; no analyst references a lens it doesn't own. | **PASS (file-level) / DEFERRED (live)** (synth static cite: 5 analyst files present in `.claude/agents/`, all `model: opus`, `name:` = role string. **LIVE `initial_read` rows DEFERRED to W3 capstone F21/AC-7** — operator-gated council run) |
| **F16 Synthesizer orchestration** | `.claude/agents/prism-synthesizer.md` | During F21: synthesizer generates `run_id`, kicks off all 5, facilitates clarifying Q&A, decides debate, integrates, writes synthesis + MARKET_PRISM row. | `run_id` immutable across the run; `phase=synthesis` row present; conditional-debate protocol respected (see F17); a genuinely integrated read (cross-lens reasoning, not a concatenation — AC-7 in phase-2 plan). | `prism-synthesizer.md` debate protocol (≤3 rounds, clarifications ≠ debate) matches the runbook + phase-2 plan. | **PASS (file-level) / DEFERRED (live)** (synth static cite: `prism-synthesizer.md` present, `model: opus`. **LIVE orchestration (run_id, kickoff, synthesis row) DEFERRED to W3 capstone F21/AC-7**) |
| **F17 Debate/clarification protocol** | synthesizer + analysts; audit `phase` tags | In F21: confirm clarifications are tagged `phase=clarification`, debate rounds `phase=debate_round_N` ONLY on genuine disagreement, and ABSENT when analysts converge. | Audit trail shows correct phase tags; NO spurious `debate_round_*` rows when reads converge; ≤3 debate rounds if disagreement; synthesizer derived availability from audit rows (F17 protocol), not inbox. | Runbook step 4/7 + phase-2 AC-3/AC-4/AC-5 match observed trail behavior. | **DEFERRED (W3)** (debate/clarification phase tags observable only in a live council trail → W3 capstone F21/AC-7, operator-gated. Not verifiable in Wave 1) |
| **F18 One MARKET_PRISM row/run** | `database.insert_advisor_observation` (`:1053`, `is_advisory_only=1`) | After F21: `SELECT count(*) ... WHERE advisor_role='MARKET_PRISM' AND raw_response run_id=<rid>`. | Exactly 1 row; `is_advisory_only=1`; `verdict` matches synthesizer's `overall_sentiment`; `run_id` matches the audit trail. | DECISIONS DE-ML-003 (MARKET_PRISM advisory-only, DB-enforced flag) matches. | **PASS (mechanism) / DEFERRED (capstone count)** (mechanism: `is_advisory_only=1` hardcoded at SQL layer `database.py:1069–1091` — cluster1-groupB.md F11. **Exactly-one-row-per-`run_id` count DEFERRED to W3 capstone F21/AC-8** — needs a real council `run_id`) |
| **F19 Overview tab render** | `templates/ai_advisor.html:942-976`; `database.get_latest_market_prism_summary` (`:1180`); `app.py:2948-3019` prefetch | Load `GET /ai-advisor` on live :8090 after F21; capture a screenshot; **Read it with the Read tool (eyes-on)**; describe chip + rationale + per-lens digest + cited sources. Also verify the empty-state arm (no row) renders informatively. | The rendered block shows the capstone sentiment chip (correct semantic class), rationale text, per-lens digest (5 lenses with available flags), and clickable cited sources; empty-state arm renders the informative message, not a blank/error. PM describes the screenshot before asserting. | CLAUDE.md `templates/ai_advisor.html` Overview row matches what renders (chip/rationale/digest/sources/empty-state). | **PASS-WITH-FINDING** (render-gate.md: live :8090 eyes-on — sentiment chip `prism-sentiment-chip--risk-on` semantic class, rationale prose, 5 lenses `prism-lens--available`, real sources, 0 console errors, zero raw-color leakage. **FINDING RF-1 — content-readability FAIL:** all 5 lens cards render raw `json.dumps(payload)` not a human digest, root cause `lens_pipeline.py:166–167` + `templates/ai_advisor.html:994–995`. Confirmed by synth code read + ux eyes-on. Follow-on fix cycle) |
| **F20 C1 model config** (MERGED @ 73dc603; deploy ⏳) | `advisors/lens_pipeline._synthesize_via_claude` (`:244`); env-read `:285`; accessor `ai_advisor.resolve_advisor_model()` (`:63-69`); 2nd call site `ai_advisor.py:1639` | **CODE check (NOW):** static-cite that `:285` reads `ADVISOR_SYNTHESIS_MODEL` default `claude-opus-4-8` and `grep` confirms zero `claude-haiku-4-5-20251001` on the synthesis path. **LIVE check (AFTER PM post-close deploy):** the deployed daemon's synthesis path reads the env var; confirm tests don't fire real Opus. | Code: `:285` reads the env var; default resolves to `claude-opus-4-8`; no Haiku literal. Live (post-deploy): the running tree picks up the env var. | DECISIONS / `docs/generated/` document `ADVISOR_SYNTHESIS_MODEL` + default; CLAUDE.md `lens_pipeline` row already says the configurable model (no surviving "Claude Haiku synthesis" wording). **Confirm no stale "Haiku" survives in `docs/generated/`/DECISIONS.** | **PASS (Wave 1 code) / DEFERRED (Wave 2 live)** (cluster1-groupB.md F13 + synth re-cite: `lens_pipeline.py:285` + `ai_advisor.py:1639` read `ADVISOR_SYNTHESIS_MODEL` default `claude-opus-4-8`; accessor `:63–69`; zero Haiku literals. **Live-against-deployed-daemon DEFERRED to W2** — PM post-close deploy. `[interpretation]` `:285` reads env directly not via `resolve_advisor_model()` — minor cohesion dup, not a defect) |

### Capstone — Phase-3 observed multi-analyst run (F21)

| Feature | Live E2E check | Expected evidence (PASS) | Doc-accuracy | P/F |
|---|---|---|---|---|
| **F21 Observed proof run** | PM drives the real `prism-synthesizer` Agent Team once on real data under direct observation (single `run_id`, real Opus spend). Follows `market-prism-runbook.md` steps 1–8. | (1) One real integrated `MARKET_PRISM` row — NOT "Synthesis unavailable", NOT a stub, NOT degenerate `limited-inputs` when lenses have data; (2) `get_prism_audit_for_run(run_id)` returns the full trail (≥5 initial_read + clarifications + any debate + 1 synthesis); (3) exactly one row per run_id; (4) Overview renders it (F19, eyes-on); (5) PM surfaces both artifacts + operator note (lens coverage, debate summary, Opus spend) and receives sign-off. | The runbook + phase-3 plan describe exactly this run; reconcile the overview epic-status (🟡 in progress) to reflect closeout outcome. | **DEFERRED (W3, operator-gated)** (capstone observed multi-analyst council run incurs real Opus spend + needs operator observation → operator's call, AC-7/AC-11. Synth prepares the runbook + artifacts; does NOT execute. Not a Fail) |

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
| **F22 assemble_advisor_context** | `ai_advisor.assemble_advisor_context` (`:1430`); route resolves NAME→hash from `bot_state`, passes `composer_symphony_id` | For a real live symphony, drive the context assembly via the suggest route; confirm the Composer `/score` call uses the **hash**, not the display name (the hash-not-name rule). | Context assembles without an HTTP 400 from Composer; passing a name would 400 — confirm a real hash was sent; `autotune_run` honoring (pre-fetched row skips internal fetch). | CLAUDE.md `ai_advisor.py` row + Architecture Constraint #6 (hash-not-name) match live behavior. | **PASS** (cluster2-group-H.md F22: hash-not-name `ai_advisor.py:1506–1509`; route resolves NAME→hash + passes `composer_symphony_id=symphony_id` `app.py:3631–3650`. `[interpretation]` live `bot_state` empty in worktree DB → reachable-path confirmed via route code, not a live /score call) |
| **F23 request_suggestions** | `ai_advisor.request_suggestions` (`:1595`) → `POST /ai-advisor/suggest` (`app.py:3619`) | Call `POST /ai-advisor/suggest` on the live page for a real symphony; force an error arm (no API key / bad symphony). | Returns suggestions JSON on success; on any error returns `type(exc).__name__` only (D-1) — no `str(exc)`, no key, no traceback in the response or the daemon log surface. | CLAUDE.md "request_suggestions (D-1 fully honored: all error paths return type(exc).__name__ only)" matches. | **PASS** (cluster2-group-H.md F23: 3-layer D-1 — client-construct `ai_advisor.py:1623`, parse `:1645`, route outer `app.py:3677–3683` all `type(exc).__name__` only; `exc_info` server-side only) |
| **F24 build_assessment_from_context** | `ai_advisor.build_assessment_from_context` (`:1368`) | On a symphony where all trials were haircut-rejected, observe the assessment block. | The block renders an **informative** empty-state explaining `oos_alpha=None` (haircut-rejected, not an error) — NOT a blank or an error toast. | CLAUDE.md gotcha "AI Advisor empty suggestions... Expected" + `build_assessment_from_context` per-symphony empty-state match. | **PASS** (cluster2-group-H.md F24: `oos_alpha is None` branch `ai_advisor.py:1409–1416` returns an informative non-empty `summary` string (FDR-strictness explanation), never `None`/blank/error toast; dict keys `:1428–1434`) |
| **F25 7-item allowlist** | `_SUGGESTIBLE_ALLOWLIST` (`:1718`), `enforce_suggestion_allowlist` (`:1743`) | Submit (or simulate) an accept whose `config_key` is OUTSIDE the 7-item allowlist; confirm structural rejection. | Any key not in {6 Optuna search-space keys, `MAX_SQUEEZE_FLOOR`} is rejected before any write; `LIVE_EXECUTION`/credential keys never accepted. | CLAUDE.md "7-item suggestible allowlist (6 Optuna search-space keys + MAX_SQUEEZE_FLOOR)" matches the code set exactly. | **PASS** (cluster2-group-H.md F25: `_SUGGESTIBLE_ALLOWLIST = frozenset(_OPTUNA_SEARCH_SPACE_KEYS) | {MAX_SQUEEZE_FLOOR}` `ai_advisor.py:1725`; `enforce_suggestion_allowlist:1750–1776` partitions allowed/rejected; `LIVE_EXECUTION` NOT in set (direct import)) |
| **F26 C2 safety gates** | `POST /ai-advisor/accept` (`app.py:3686`, "all three C2 safety gates"); `POST /ai-advisor/reject` (`:3762`) | Drive a real accept through the three gates (allowlist + OOS re-validation + risk-direction cross-check); drive a reject. | Accept applies only after all 3 gates pass and writes the allowlisted .env key; a gate failure blocks the write; reject records the rejection with **no** config write. | DECISIONS / CLAUDE.md "C2 safety gates" enumerate the same three gates that fire live. | **PASS-WITH-DOC-NIT** (cluster2-group-H.md F26: Gate-1 allowlist blocks (live test-client: out-of-allowlist `status=rejected`); reject route no-write (live); Gate-3 OOS-revalidation blocks `app.py:3711–3725`; config write `:3733–3735` only after gates. **AC-18 doc nit:** code has 4 gates, Gate-2 risk-direction LOGS-only (not block) `:3708–3709` — CLAUDE.md "C2 safety gates" wording reconciled by closeout-doc) |
| **F27 FDR strictness** | CRRA-EU + Harvey-Liu FDR (autotuner/`acceptance_gate`); advisor consumes | Confirm that an empty-suggestions result on a strict symphony is the **expected** strict-gate outcome, not a failure. | Empty suggestions accompanied by the assessment explaining strictness (F24); no error state. | Gotcha "CRRA-EU + Harvey-Liu FDR gate is intentionally strict" matches. | **PASS** (cluster2-group-H.md F27: empty `suggestions` returns a non-error `(ConfigSuggestionsResponse(suggestions=[]), None)` `ai_advisor.py:1613–1615`; route returns `{suggestions:[], assessment}` `app.py:3676` no error field; matches the documented strict-gate gotcha) |

### Group I — Correlations / Asset Swaps / Logic Changes tabs (F28–F30)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F28 Correlations tab** | `correlation_diagnostic.compute_pairwise_correlations` (`:196`) ← prefetch `app.py:2910` | Load `GET /ai-advisor`, switch to Correlations; with ≥2 live symphonies having return series, observe the rendered correlation matrix. | A real pairwise correlation matrix renders (values in [-1,1]); the informative empty/insufficient-data state renders when <2 series exist. Eyes-on screenshot read. | CLAUDE.md SPA row lists Correlations as a live tab; `correlation_diagnostic` producer documented + matches. | **PASS** (cluster2-group-I.md F28 + render-gate.md: real `compute_pairwise_correlations` `correlation_diagnostic.py:196` (not stub), exception-isolated prefetch `app.py:2896–2913`; live :8090 eyes-on full 78-pair matrix w/ real data + token color coding) |
| **F29 Asset Swaps tab** | `asset_swap_engine.propose_operator_swap` (`:912`) ← `POST /ai-advisor/asset-swaps/evaluate` (`app.py:3042`, CSRF) | POST a real swap evaluation for a live symphony (with CSRF token); inspect the returned candidates + persisted observation. | Returns objective-directed candidates ranked with the `_apply_lens_blend` blend (weight 0.25); survivors passed the BHY-FDR gate; an `ASSET_SWAP` observation persists with `lens_evidence`+`sources` (`is_advisory_only=1`). | DECISIONS DE-CY3-001 (lens blend, weight 0.25, gate unchanged, persistence contract) matches live. | **PASS (route+gate+blend) / DEFERRED (live POST)** (cluster2-group-I.md F29: route chain `app.py:3042→3113–3124` real `propose_operator_swap`; `LENS_BLEND_WEIGHT=0.25` `asset_swap_engine.py:78`, `_apply_lens_blend:372`; BHY-FDR `evaluate_candidate_batch` on full batch; D-1 `app.py:3125–3130`. **Live POST w/ real Composer key DEFERRED to W2/AC-14** — market-hours + no-key. `[interpretation]` ASSUMPTION-I-1: `_persist_observation` body (lens_evidence+sources) not fully read) |
| **F30 Logic Changes tab** | `logic_change_engine.propose_operator_logic_change` ← `POST /ai-advisor/logic-changes/evaluate` (`app.py:3183`, CSRF) | POST a real logic-change evaluation for a live symphony (with CSRF token); inspect candidates + persisted observation. | Returns objective-directed logic tweaks gated by BHY-FDR; a `LOGIC_CHANGE` observation persists (`is_advisory_only=1`); D-1 on error. | CLAUDE.md/DECISIONS logic-change engine docs match the live route behavior. | **PASS (route+gate) / DEFERRED (live POST)** (cluster2-group-I.md F30: route chain `app.py:3183→3263–3274` real `propose_operator_logic_change`; full-batch BHY-FDR `logic_change_engine.py:1254/1281/1431`, Yekutieli c(n) exposed `app.py:3290–3297`; D-1 `:3275–3280`. **Live POST w/ real Composer key DEFERRED to W2/AC-14**) |

### Group J — Chat tab M5 (F31)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F31 Chat explain-only** | `advisor_chat.explain_artifact` (`:337`), `validate_artifact` (`:167`), `CHAT_ARTIFACT_ALLOWED_FIELDS` (`:74`) ← `POST /ai-advisor/chat/send` (`app.py:3803`) | Send a real chat question against a scoped artifact; ALSO attempt an artifact with a field outside the allowlist + a question that tries to trigger a trade/write. | A real LLM explanation returns; the out-of-allowlist field is **stripped** by `validate_artifact` (re-validated inside `explain_artifact`, defense-in-depth); the chat NEVER calls OOS re-validation, `suggest_swaps`, or `run_backtest` and NEVER writes config — the hard explain-only boundary holds; LLM error → 200 JSON `{error}` (D-1). | `m5-chat-hardening.md` + `security-review-m5-chat.md` + CLAUDE.md (M1–M4+M6+multi-lens fields, explain_artifact re-validation) match the live boundary. | **PASS** (cluster2-group-J.md F31: dual-layer `validate_artifact` (route `app.py:3883` + `explain_artifact` entry `advisor_chat.py:370`); grep `suggest_swaps\|run_backtest\|save_state\|insert_advisor_observation` in advisor_chat.py = 0; explain-only system prompt `:261–296`; D-1 `:376–400`) |

### Group K — Strategy Builder tab (F32–F34)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F32 propose_strategies** | `strategy_builder_engine.propose_strategies` (`:864`) ← `POST /ai-advisor/strategy-builder/run` (`app.py:3394`, CSRF) | POST a real Strategy Builder run (objective + universe, CSRF token) on the live page; inspect survivor/rejected/FDR JSON + the Strategy Builder tab render. | Returns T1–T7 template candidates backtested + a **single-batch** FDR gate result (`evaluate_candidate_batch` over the full batch); `ScreenConfig` post-gate screens applied; survivors persisted; advisory-only (not in `_SETTINGS_WRITE_ALLOWLIST`); no `LIVE_EXECUTION`. Eyes-on the rendered survivor/rejected cards. | CLAUDE.md `strategy_builder_engine.py` row + phase-2/3/4 contracts match; **confirm the run is template-only in prod (HF-1) and the doc's "injected at the route boundary" claim is reconciled.** | **PASS (template-only in prod) / DEFERRED (live POST)** (cluster2-group-K.md F32: `propose_strategies` single-batch gate `evaluate_candidate_batch(bt_candidates)` BEFORE screens `strategy_builder_engine.py:964–969`; screens only on survivors `:971–980`; advisory-only persistence `_persist_survivor`; route not in allowlist `app.py:3405`. **Run is template-only in prod — `app.py:3437` no `community_candidates=` (HF-1, see F35).** Live POST DEFERRED to W2/AC-14) |
| **F33 symphony_schema** | `advisors/symphony_schema.py` (`validate_tree`/`lint_tree`/`extract_tickers`/`render_rules_text` + 10 constructors) | Through F32: confirm the built trees pass `validate_tree` (no HARD errors) and `render_rules_text` produces readable rules; feed a deep/oversized tree to confirm lint-only (not raise). | Trees validate; `lint_tree` returns soft warnings (size/depth caps + unknown indicator fns are lint-only, never raise); deterministic rules text; never-raising on arbitrary input. | CLAUDE.md `symphony_schema.py` row (never-raising, lint-only caps, 10 constructors, depth-230 safe) matches; vocabulary pinned by `strategy-builder-composer-grammar.md`. | **PASS** (cluster2-group-K.md F33 runnable: `validate_tree` errors=[], `render_rules_text` non-empty, `lint_tree` never-raises, depth-230 no RecursionError, unknown-fn lint-only; constructors `symphony_schema.py:784–1115`) |
| **F34 composer_backtest_client** | `composer_backtest_client.run_backtest` (1 req/s, `:30`; 429 backoff `:332`) | Through F32: confirm backtests are paced at ≤1 req/s and a 429 triggers the documented backoff. | Backtest calls respect the Composer 1 req/s limit; a 429 sleeps per `Retry-After`/`_BACKOFF_INTERVALS`; no rate-limit storm. | CLAUDE.md/strategy-builder docs state "1 req/s" — matches the client constant + comment. | **PASS (429 backoff) / PASS-DOWNRANKED (1 req/s)** (cluster2-group-K.md F34: 429 backoff `_BACKOFF_INTERVALS=(1,2,4,8)` `composer_backtest_client.py:55`, Retry-After honored `:330–339`. `[interpretation]` ASSUMPTION-K-1: 1 req/s pacing is IMPLICIT — relies on Composer response latency ≥1s, no explicit `sleep(1)` in the engine loop. MED confidence, non-blocking) |

### Group L — Community strategies + proposal/gate infra (F35–F37)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F35 Community strategies** (HF-1) | `community_strats.load_community_strategies` (`:98`), `community_candidate_infos` (`strategy_builder_engine.py:195`) — **NO production route caller** | Two-part: (a) Verify the engine layer directly — call `load_community_strategies` (atlas_cache weekly TTL: a 2nd call within TTL hits cache, not Mongo; structural-hash dedup; sharpe filter) and adapt via `community_candidate_infos`. (b) **Verify the PRODUCTION GAP:** confirm `app.py:3437` passes NO `community_candidates` and no route fetches Atlas community strats. | (a) loader returns `{available,candidates,stats,source}`, cache protects the provider bill, dedup + sharpe filter work; (b) the Strategy Builder route runs **template-only** in prod — community strats are NOT reachable from the live UI. **This is a finding, not a pass:** decide build-gap vs deferred-by-design with the operator. | **HF-1 / stale-doc:** CLAUDE.md `community_strats.py` row says "first production caller: propose_strategies via the community_candidate_infos adapter (injected at the route boundary)" — FALSE in prod (no route injection). DECISIONS:633 ("no production caller yet") is closer to truth. Reconcile both to the verified state. | **FINDING — OPERATOR-GATED (AC-17)** (cluster2-group-L.md F35: engine layer built `community_strats.py:98` + `strategy_builder_engine.py:195/864/921–922`; **HF-1 HOLLOW IN PROD CONFIRMED** — `app.py:3437` no `community_candidates=`, grep app.py=0. Doc contradiction: CLAUDE.md "injected at the route boundary" FALSE; DECISIONS:633 TRUE, :651 implies a nonexistent caller. Doc reconciled by closeout-doc; build-vs-defer = operator's call) |
| **F36 BHY-FDR gate** | `backtest_gate_engine.evaluate_candidate_batch` (BHY/Yekutieli FDR across the FULL batch) | Through F29/F30/F32: confirm the gate runs FDR across the full candidate set (template + any community together), not per-candidate; screens never shrink the gate input. | The FDR correction's N = full candidate count; survivors are the BHY-adjusted significant set; the anti-overfit invariant (gate input = full batch) holds. | CLAUDE.md strategy-builder row + DECISIONS community-wiring (single-batch FDR invariant) match; gate applies BHY/Yekutieli (Harvey-Liu 2015). | **PASS** (cluster2-group-L.md F36: `evaluate_candidate_batch` N=`len(candidates)` full batch `backtest_gate_engine.py:519/549–563`; receives `bt_candidates` pre-screen `strategy_builder_engine.py:964–969`; screens post-gate only `:971–980` — anti-overfit invariant holds) |
| **F37 acceptance_gate** | `acceptance_gate.py` (shared by autotuner + advisor proposal suite) | Confirm the same reusable gate object governs both the autotuner and the advisor proposal suite (one acceptance contract, not two divergent ones). | The advisor suite and autotuner invoke the same `acceptance_gate` logic; no divergent duplicate gate. | CLAUDE.md `acceptance_gate.py` row ("used by autotuner and AI Advisor proposal suite") matches both call sites. | **PASS** (cluster2-group-L.md F37 runnable: `acceptance_gate` module id identical via direct import AND via `backtest_gate_engine` → same module object; `autotuner.py:14/2685` + `backtest_gate_engine.py:73` — one shared contract, no divergent duplicate) |

### Group M — Unified SPA shell (F38–F40)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F38 SPA 6-tab shell** | `templates/ai_advisor.html` (6 tabs, one server render); `static/ai_advisor.js` `initTabSwitcher` | Load `GET /ai-advisor` (`app.py:2848`) live; switch through all 6 tabs (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) in-place; `node --check static/ai_advisor.js`. | All 6 tabs render in one page; tab switching is in-place (no full reload); JS parses clean (`node --check` passes); eyes-on screenshot confirms each tab's panel renders (not blank/error). | CLAUDE.md `templates/ai_advisor.html` + `static/ai_advisor.js` rows (6 in-place tabs, `initTabSwitcher`) match. Confirm the deleted per-tab templates are NOT recreated (gotchas). | **PASS** (cluster2-group-M.md F38 + render-gate.md: GET /ai-advisor 200, all 6 `tab-panel-*` present; `node --check ai_advisor.js` EXIT 0; live :8090 eyes-on all 6 tabs in-place switch, 0 console errors; deleted per-tab templates absent) |
| **F39 GET redirects** | 5 GET sub-routes 302→`/ai-advisor` (`app.py:3023/3033/3174/3381/3794`) | `curl -sI` each of `/ai-advisor/correlations`, `/asset-swaps`, `/logic-changes`, `/chat`, `/strategy-builder` on :8090. | Each returns 302 with `Location: /ai-advisor`; the standalone per-tab pages no longer exist. | CLAUDE.md "all 5 GET sub-routes 302-redirect to /ai-advisor" + the Strategy-Builder-template-deleted gotcha match. | **PASS** (cluster2-group-M.md F39: live :8090 probes — all 5 sub-routes (`/correlations`,`/asset-swaps`,`/logic-changes`,`/chat`,`/strategy-builder`) return 302 `Location:/ai-advisor`; `redirect(url_for("ai_advisor_tab"),302)` `app.py:3023/3033/3174/3381/3794`) |
| **F40 CSRF on POSTs** | CSRF infra `_validate_csrf`/`_csrf_before_request`; POST routes `accept/reject/suggest/*-evaluate/chat-send/strategy-builder-run` | POST to a protected route WITHOUT a CSRF token (live, non-pytest) → expect rejection; WITH a token → accepted. Confirm none of these routes is in `_SETTINGS_WRITE_ALLOWLIST`. | A tokenless POST is rejected (403/CSRF error); a valid-token POST proceeds; all advisor action routes are advisory-only — none can write `LIVE_EXECUTION` or credential keys. | CLAUDE.md Architecture Constraint #2 (two guarded write paths; advisor routes NOT trade surfaces) + the strategy-builder route "not in allowlist" claim match. | **PASS** (cluster2-group-M.md F40: tokenless POST /suggest → 403 (live test-client, `_csrf_check_enabled=True`); `_csrf_before_request` on ALL POSTs `app.py:183–187`; advisor routes NOT in `_SETTINGS_WRITE_ALLOWLIST` (10 keys, `LIVE_EXECUTION` absent — direct import)) |

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
   the running :8090 tree — gates AC-1 and the F13/F20 doc reconciliation.
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

---

## Execution Plan — Cluster Briefs, Sequencing & Coordination (added by closeout-synth @ 73dc603)

### Roster & ownership
| Agent | Cluster | Owns | Reports to |
|---|---|---|---|
| `closeout-audit-prism` | Cluster 1 (F1–F21) | Lens producers, warehouse, audit-log foundation, nightly pipeline, scheduler, council agent files | `closeout-synth` (peer SendMessage) |
| `closeout-audit-suite` | Cluster 2 (F22–F40) | Config Advisor core, Correlations/Asset-Swaps/Logic-Changes tabs, Chat M5, Strategy Builder, Community-strats (HF-1), FDR/gate infra, SPA-shell routes/static (`node --check`, 302 redirects, CSRF) | `closeout-synth` (peer SendMessage) |
| `closeout-ux` (ux-expert) | Visual render gate (AC-9 + AC-13) | The BINDING eyes-on screenshot read of the running daemon's `GET /ai-advisor` Overview block + all 6 tab panels (read-only Playwright) — sentiment-chip semantic classes, design-token usage, zero raw-color leakage, informative empty-states; describes each screenshot before asserting | `closeout-synth` (peer SendMessage) |
| `closeout-doc` | Doc-accuracy (AC-10/AC-18) | Lands doc corrections on the branch AFTER synth confirms findings | `closeout-synth` |
| `closeout-synth` (me) | Synthesis lead | Reconciles findings → ONE verdict; sole committer | PM (`team-lead`) |

### Per-feature probe method (evidence required for EVERY row)
Each feature gets **both** of: a **`file:line` cite** AND a **runnable result** from one of these
four allowed methods (down-rank any finding with neither):
1. **Flask test client** (`app.test_client()`) — renders + POST routes WITHOUT a live daemon.
   Use this for all SPA-render + route-shape checks (CSRF is disabled under pytest, so
   construct the client with the live CSRF path OR assert the tokenless-reject arm separately).
2. **Direct producer/engine call** — `import ai_advisor; ai_advisor._build_<lens>_section()`,
   `strategy_builder_engine.propose_strategies(...)`, etc., run as a one-shot Python probe.
3. **Read-only SQLite** — `sqlite3` `SELECT` against the live `alphabot_state.db` /
   `alphabot_warehouse.db` (open read-only; NEVER write the state DB).
4. **Static cite** — read the exact lines; quote them. Sufficient ALONE only for pure
   structural claims (e.g. "route returns 302", "no `community_candidates` arg at `:3437`").

### Sequencing by dependency (3 waves)
**WAVE 1 — NOW (C1-independent, deploy-independent — the vast majority).** Everything
verifiable via the four methods above against the worktree code + live DB read-only:
- **Cluster 1:** F1–F12 (lens both-arms via direct `_build_*_section` calls + forced failure
  arms; warehouse round-trip + secret-strip on a TEMP db_path NOT the real warehouse;
  audit-log CLI + migration-032; universe floors; honest-availability contract; citation
  validation). F14 scheduler wiring (static cite of `app.py:443`). F15–F18 council-agent
  *file* checks (model:opus, role strings) — the LIVE council run (F21) is operator-gated.
- **Cluster 2:** F22–F40 ALL runnable now via test client + direct calls + read-only SQL +
  static cites. HF-1 (F35) is a static-cite + grep finding — already RE-CONFIRMED above.
- **VISUAL RENDER GATE (AC-9 + AC-13) — WAVE 1, BINDING (`closeout-ux`):** a READ-ONLY
  Playwright screenshot of the RUNNING daemon's `GET /ai-advisor` Overview block + each of
  the 6 tab panels. `GET /ai-advisor` is the read-only dashboard route (Arch Constraint #5),
  not the execution path — market-hours-safe. C1 did not touch templates, so the render is
  valid pre-deploy (only synthesis CONTENT is Wave 2). `closeout-ux` describes each
  screenshot before asserting; synth folds the evidence into AC-9/AC-13. The integration
  auditor's `node --check static/ai_advisor.js` + test-client 200/shape checks are
  necessary-but-not-sufficient companions to this binding visual gate.
- **F13 partial:** a `run_pipeline(dry_run=True)` shape check + the CODE cite that `:285`
  reads the env var. (The non-dry-run live write waits for Wave 2.)
- **F20 partial:** the CODE check (env-var read + zero Haiku literal) — done now.

**WAVE 2 — AFTER PM post-close deploy (~14:00 MDT; PM signals "deploy done").** Only the
checks that need the DEPLOYED daemon in memory:
- F13/F20 nightly synthesis-model behavior against the deployed daemon.
- A live `run_pipeline()` **non-dry-run** = exactly ONE call, post-deploy, **timed clear of
  the 03:00 scheduler**, clearly labeled; writes ONE real MARKET_PRISM row — confirm it does
  NOT double-write a row for the same logical night; advisory-only, off the execution path.
- A POST-DEPLOY re-render of the Overview tab (`closeout-ux`) ONLY to confirm the freshly
  synthesized CONTENT (the new MARKET_PRISM row) displays with the C1 model. The RENDER
  itself was already gated in Wave 1; this Wave-2 pass verifies synthesis-content freshness,
  not template correctness.

**WAVE 3 — OPERATOR-GATED (PREPARE + flag ONLY; do NOT execute):**
- **AC-7 / F21** — capstone observed multi-analyst council run (real Opus spend). Prepare
  the runbook + the artifacts the operator needs; mark as the operator's call.
- **AC-11** — operator sign-off gate (Phase-4 stays hard-blocked until received).
- **AC-17 / F35** — HF-1 community-strats adjudication (build-the-injection vs
  deferred-by-design). Produce the adjudication brief; the decision is the operator's.

### Coordination protocol
- Auditors message `closeout-synth` (me) peer-to-peer with findings AS they complete groups
  (don't batch the whole cluster into one message — send per-group so I can cross-verify
  early). Each finding: feature id, PASS/FAIL/OPERATOR-GATED, `file:line`, the runnable
  result (command + key output), and any `[interpretation]` label.
- I (synth) adversarially cross-verify each material claim against the worktree code before
  promoting it into the verdict. A claim resting on file-scoped grep rather than a
  reachable-path check is down-ranked.
- `closeout-doc` is **BLOCKED** until both auditors report and I confirm the doc-accuracy
  findings (so corrections land against verified truth, not a draft). Confirmed targets are
  already enumerated in the refresh banner (HF-1 doc reconciliation; C1 stale-"Haiku" sweep;
  the "macro stub" mislabel hunt).
- I am the **sole committer**. Auditors + doc-writer write to the branch but do NOT commit
  (index-lock races). When the matrix is executed (minus Wave-3 operator gates), I commit
  the verdict + doc corrections ONCE and report `cycle complete` to the PM with the doc path
  + headline verdict. **No merge / no push / no PR — the PM gates.**

### PM directives on the two assumptions (resolved 2026-06-17)
- **AC-9 / AC-13 eyes-on render gate is BINDING and WAVE 1 — `[PM-ASSUMED]` (a) REJECTED by
  the PM.** A test-client 200 + 0-console-errors is necessary but NEVER sufficient for
  visual correctness (the operator's hardest UI lesson). The eyes-on screenshot read is the
  binding evidence for AC-9 (Overview) AND AC-13 (all 6 tabs). It is satisfiable NOW (no
  deploy needed) via a **READ-ONLY Playwright screenshot of the RUNNING daemon's
  `GET /ai-advisor` + each tab** — `GET /ai-advisor` is the read-only dashboard route
  ("Templates open SQLite read-only; UI never reruns the engine", Architecture Constraint
  #5), never the execution path, so it is market-hours-safe. C1 touched only the synthesis
  MODEL, not templates, so the RENDER gate is valid pre-deploy; only synthesis-CONTENT
  freshness is Wave 2. **A 4th teammate `closeout-ux` (ux-expert) OWNS this gate** (see
  roster). The integration auditors verify routes/engines/DB/static; the ux-expert does the
  visual gate; synth cross-verifies + folds its screenshot evidence into AC-9/AC-13.
- **AC-17 / HF-1 stays OPERATOR-GATED — `[PM-ASSUMED]` (b) AFFIRMED.** The verdict flags
  HF-1 and reconciles the docs (DECISIONS.md `:633`/`:651` + the CLAUDE.md "injected at the
  route boundary" row + the MEMORY/INDEX entry) to verified state, but does NOT presume
  defer-vs-build. **PM note:** the operator leans BUILD the wiring — but that is a SEPARATE
  Toxic-Pair cycle AFTER closeout. The closeout VERIFIES; it builds nothing.
