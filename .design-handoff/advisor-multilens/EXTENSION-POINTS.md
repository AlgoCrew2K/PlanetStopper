# AI Advisor — Extension-Points Map (for the multi-lens portfolio analyst)

**Source:** Explore agent (opus), 2026-06-10. All paths absolute under repo root. The existing AI Advisor is a mature, advise-only, OFFLINE subsystem (M1–M5) with a hard read-only boundary. This maps where every new piece (5 lenses, news citations, capital-allocation candidates, scheduled pipeline) plugs in.

---

## 1. Context Assembly — `ai_advisor.assemble_advisor_context`

`assemble_advisor_context(scope, symphony_id, composer_symphony_id, autotune_run)` at **`ai_advisor.py:451-545`** builds one flat dict ("context blob") JSON-serialized straight into the Claude prompt (`_build_messages`, `ai_advisor.py:577-593`).

Current context domains (dict at `ai_advisor.py:526-544`):
- `scope`, `symphony_id` (`:527-528`)
- `role_framing` — `_ROLE_FRAMING` (`:171-180`), injected `:530`
- `suggestible_surface` — 7-item allowlist (6 Optuna keys + `MAX_SQUEEZE_FLOOR`), `_build_suggestible_surface` (`:316-343`); each item carries definition, risk_polarity, valid_range, current_live_value, locked flag — `:532`
- `locked_vars` — `_read_current_strategy` (`:346-380`), `:533`
- `optuna_evidence` — `_build_optuna_section` (`:273-306`): train_alpha, oos_alpha, oos_train_gap, fallback/default_oos_alpha, baseline_decision — `:535`
- `volatility_regime` — `_build_volatility_regime` (`:218-270`); **currently always `available: False`** (cols don't exist in `autotune_runs`) — `:537`
- `data_window` — `_DATA_WINDOW` (`:159-169`), `:539`
- `risk_invariants` — `_RISK_INVARIANTS` (`:151-156`), `:541`
- `symphony_logic` — condensed Composer tree from `symphony_logic.get_condensed_logic` (`:524`), `:543`

**Allowlist is a hard real-money governance boundary** (`ai_advisor.py:9-11, 60-66, 472-475`): never reads `dict(os.environ)`/`.env`; anything not enumerated is structurally excluded. New lens data adds *market/analytical* context but must NOT introduce credentials, account IDs, or safety flags.

**Where new lens domains plug in:** add new top-level keys to the dict at **`ai_advisor.py:526-544`**, each fed by a new `_build_<lens>_section(...)` helper following `_build_optuna_section`/`_build_volatility_regime` — the **`available: bool` + `reason` honest-availability pattern** (data-wall enforcement at `:234-270`). E.g. `technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`, `algo_db`, and a `cited_sources` list. The `_SENTINEL`/`autotune_run` injection pattern (`:503-518`) is the template for a caller pre-fetching lens data and passing it in (avoids redundant fetches; keeps it test-mockable).

**`build_assessment_from_context`** (`ai_advisor.py:389-448`) is a pure function over the assembled context — reads only `context["optuna_evidence"]` (`:410`), produces a per-symphony human-readable `summary` + raw OOS scalars (because `ConfigSuggestionsResponse` has no response-level summary). Route calls it at `app.py:3340-3343`. A **multi-lens assessment** extends this to read new domains and synthesize a cross-lens summary.

**Pydantic output contract:** `ConfigSuggestion` (`:188-200`) + `ConfigSuggestionsResponse` (`:203-210`). New suggestion *types* (swaps, capital moves) need new schemas or a discriminated union. This Claude config-suggestion path is **separate** from the backtest-gated swap/logic engines (§2).

---

## 2. Suggestion Engines (`advisors/`)

All five OFFLINE only, never imported from `alpha_bot_execution.py`, only read/inline-backtest Composer endpoints (no writes). Hard constraints in each module header.

### `advisors/backtest_gate_engine.py` — the gate spine
- Input: `BacktestCandidate` NamedTuple (`:133-178`).
- Entry: `evaluate_candidate_batch(candidates, incumbent_oos_alpha, default_oos_alpha)` (`:463-669`). Fold-transforms each (`_fold_transform_single`, `:365-455`), runs **BHY/Yekutieli FDR across the batch** (`n_effective = len(candidates)`, `:557-563`), picks the BHY winner (`:575-590`), calls `acceptance_gate.evaluate_acceptance_gate` per candidate (`:635-647`).
- Output: `GatedBatch` (`:216-237`) of `CandidateGateResult` (`:181-214`) — `verdict.decision` ∈ {ADOPT_CANDIDATE, KEEP_INCUMBENT, REJECT_VETO_FAILED}.
- Mandatory `SURVIVOR_OVERFITTING_CAVEAT` (`:108-114`) on every survivor (`:650-651`).
- **Every backtestable candidate type must pass through this** — callers must NOT call `acceptance_gate` directly (`:471-474`). Raising N raises the bar.
- **NOTE (user decision 2026-06-10): capital-allocation candidates are backtest-AGNOSTIC — they do NOT route through this gate.** They are advisory observations only.

### `advisors/asset_swap_engine.py` — swap proposals
- Objective-directed: every swap solves a `SwapObjective` (`:79-102`) — `objective_type` ∈ {reduce_correlation, reduce_drawdown, lift_risk_adjusted}.
- Entry points: `propose_operator_swap(...)` (`:622-750`), `suggest_swaps(...)` (`:761-940`).
- **Candidate generation `generate_objective_directed_candidates(...)` (`:285-423`)** ranks `available_assets` against an objective using a `correlation_data` dict. **Exact seam where a fundamentally/sentiment-informed candidate is produced** — currently ranks purely on correlation/variance/pseudo-Sharpe (`:341-423`). A new lens supplies extra scoring signals or a new `objective_type` branch here.
- Proposal shape: `SwapProposalResult` (`:110-166`) — incl. `objective_rationale` (free text, `_build_objective_rationale` `:431-465`), `gate_result`, `apply_guidance` (plain-text "open Composer and swap X→Y manually" `:62-65`, never a button), `data_warnings`.
- Run wrapper: `SwapRunResult` (`:174-210`).
- **Cited news attaches** to free-text `objective_rationale`; for structured/clickable citations add a `sources: list` field to `SwapProposalResult` (`:149-166`) and persisted `raw_response` (`:496-506`).

### `advisors/logic_change_engine.py` — same pattern for tree params
- `LogicTweak` (`:150-185`), `LogicChangeObjective` (`:193-217`), `LogicChangeProposalResult` (`:225-275`), `LogicChangeRunResult` (`:288-323`).
- Entry: `propose_operator_logic_change(...)` (`:1119-1305`), `suggest_logic_changes(...)` (`:1308-1463`).
- `generate_objective_directed_candidates(...)` (`:528-749`); `MAX_SUGGESTED_CANDIDATES = 30` (`:93`).

### `advisors/correlation_diagnostic.py` — pure measurement (M1)
- `compute_pairwise_correlations(return_series) -> list[PairResult]` (`:196-249`). Pure: no API/gate/DB-write. `CRISIS_CAVEAT` (`:52-56`).

### `advisors/advisor_chat.py` — explain-only chat (M5)
- `explain_artifact(question, artifact) -> ChatResponse` (`:294-375`). **Hard explain-only** (`:7-16`, system prompt `:226-256`): no trade directives, no new recommendations, no write path. Reuses `ai_advisor._build_client()` (`:325`).
- **Artifact allowlist `CHAT_ARTIFACT_ALLOWED_FIELDS` (`:68-121`) + `validate_artifact` (`:135-162`).** New candidate types (swap/capital/multi-lens) + news-citation fields MUST be added here or chat strips them.

---

## 3. Persistence (`database.py`)

### `advisor_observations` — canonical advisor output table
- Schema: `migrations/017_advisor_observations.sql` (`:10-20`) + `migrations/025_advisor_observations_symphony_id.sql`. Columns: `id, created_at, advisor_role, subject_type, subject_id, verdict, raw_response (JSON), is_advisory_only (DEFAULT 1), spec_bundle_id, symphony_id`. Column constant `_ADVISOR_OBSERVATION_COLUMNS` (`database.py:1022-1033`).
- Insert: `insert_advisor_observation(...)` (**`database.py:1052-1096`**). Append-only (`:1065-1066`). **`is_advisory_only` hard-wired to 1 regardless of caller** (`:1068-1069, :1090`) — structural "never moves money." `raw_response` is the JSON field for arbitrary structured payload (ranked candidates, reasoning, **cited sources**).
- Engines write here: `asset_swap_engine._persist_observation` (`:473-507`, role `ASSET_SWAP`), `logic_change_engine._persist_observation` (`:814-850`, role `LOGIC_CHANGE`). Persist **regardless of verdict** (RC-4). `raw_response` dict (`asset_swap_engine.py:496-506`) is where ranked candidates + reasoning + new `sources`/`lens_evidence` blobs go — **no schema change needed (JSON)**.
- Reads (all `get_ro_connection()`): `get_advisor_observations_for_subject` (`:1099-1121`), `_for_role` (`:1124-1144`), `_for_symphony` (`:1147-1168`).

### `llm_suggestions` — config-suggestion accept/reject audit
- Schema `database.py:186-206`. Write `record_llm_suggestion(...)` (`:873-891+`, keyword-only, append-only). `operator_decision` (pending/accepted/rejected), before/after, `oos_revalidation`. Reads `get_llm_suggestions_for_*` (`:983, :1002`).

**For new candidate types:** `advisor_observations.raw_response` (JSON) is the natural home for ranked multi-lens candidates + reasoning + cited sources. **Capital-allocation candidate** (add X / pull Y) → new `advisor_role` (e.g. `CAPITAL_ALLOCATION`) + `subject_type` via the same `insert_advisor_observation` path, OR a dedicated migration if queryable columns needed (`from_symphony`, `to_symphony`, `amount`). Citations as first-class rows → structured `raw_response.sources = [{title, url, published, lens}]`.

---

## 4. Dashboard Review Surface

### Template + JS
- **`templates/ai_advisor.html`** — single unified SPA, 5 tab panels server-rendered, switched in-place: Overview (`:607-662`), Correlations (`:667-780`), Asset Swaps (`:785-847`), Logic Changes (`:852-925`), Chat (`:930-952`). Chat slide panel always in DOM (`:957+`). Tab buttons `:559-599`. **New candidate types get a new `<div class="tab-panel" data-tab="...">` + a tab button.** No existing news/citation/source markup anywhere.
- **`static/ai_advisor.js`** — `renderSuggestions(...)` (`:84-`) builds cards via `innerHTML` string concat (`:115-`, `escHtml` everywhere `:24-31`). Accept/reject inline (`:236-242`) → `window.acceptSuggestion`/`rejectSuggestion` (`:351-394`) POST to `/ai-advisor/accept`,`/reject`. "Discuss this" chat button (`:276-284`) carries `data-artifact-json`. Autotune sparkline/recent-runs `:406-495`. CSRF token fetched on load (`:11-18`). **Clickable news citations = new render helper here (`<a href>` list inside the card) fed by a `sources` array.**

### Routes (`app.py`)
- `GET /ai-advisor` → `ai_advisor_tab()` (`:2735-2845`) — assembles all panels server-side (observations `:2756-2778`, correlation matrix `:2784-2804`, API-key checks, symphony list). **New tabs add data here.**
- `POST /ai-advisor/suggest` → `ai_advisor_suggest()` (`:3288-3351`) — hash→name resolve, fetch autotune run, `assemble_advisor_context` + `request_suggestions` + `build_assessment_from_context`; returns `{suggestions, assessment}`.
- `POST /ai-advisor/asset-swaps/evaluate` → `ai_advisor_asset_swaps_evaluate()` (`:2867-2986`) — **NAME→Composer-hash resolution `:2911-2921` (reusable pattern)**, `fetch_symphony_score`, `propose_operator_swap`.
- `POST /ai-advisor/logic-changes/evaluate` → (`:2998+`).
- `POST /ai-advisor/accept` → `ai_advisor_accept()` (`:3354-3427`) — C2 gates (allowlist, risk-direction, OOS revalidation, locked-var), writes via `save_symphony_strategy`, audits via `record_llm_suggestion`.
- `POST /ai-advisor/reject` → (`:3430-3459`).
- `POST /ai-advisor/chat/send` → `ai_advisor_chat_send()` (`:3471-3557`) — rate-limited (`:3515-3541`), size-capped, `validate_artifact` → `explain_artifact`.
- `GET /api/advisor-observations` → (`:3575-3605`); `_ADVISOR_ROLES` (`:3565-3569`).
- Old GET sub-routes 302-redirect to `/ai-advisor` (`:2848-2864, :2989-2995, :3462-3468`).

**Surfacing new types:** new `POST /ai-advisor/<type>/evaluate` per the asset-swaps template (NAME→hash `:2911-2921`), new tab panel, reuse accept/reject. **Capital adds/pulls need a new accept semantic** — accept must NOT execute a capital move; it records an operator decision + plain-text apply-guidance only (§7).

---

## 5. Symphony / Portfolio Model

- **`bot_state`** — live engine state, JSON blob in `bot_state` table (`database.py:249`). `load_state()` (`:287-293`), `save_state()` (`:296-301`). **Keyed by Composer hash ID**; per-symphony dict has `name`, `triggered`, `current_holdings`, transient risk fields. Holdings: `sym.get("holdings", [])` with `ticker` + amount/allocation/quantity (`alpha_bot_execution.py:744-759`, `has_positive_holdings` `:122-`). NAME↔hash resolve `app.py:2911-2921` / `:3299-3308`.
- **`symphony_strategies`** (`database.py:127-134`) — per-symphony `parameters` (JSON), `locked_vars`, separate `live_mode` col (DEFAULT 0 = dry-run). Accessors `get_symphony_strategy` (`:456-479`), `save_symphony_strategy` (`:482-501`), `get_symphony_live_mode` (`:504-529`), `set_symphony_live_mode` (`:532+`). This is *risk-engine config*, not capital.
- **Composer:** `symphony_logic.fetch_symphony_score` (`symphony_logic.py:35-`), `get_condensed_logic` (`:152+`) hit `/symphonies/{id}/score`. Account-level capital from `/portfolio/accounts/{id}/total-stats` cached by `_refresh_account_totals` (`app.py:343-398`) — `portfolio_value`, `portfolio_cr`, `portfolio_mdd`. Per-symphony `last_percent_change` from `fetch_symphony_stats` (`alpha_bot_execution.py:742, :768`).

**Biggest gap for capital allocation:** **No per-symphony capital/funding/weight representation** anywhere — only holdings (tickers+amounts) and account-level totals. `feature-plans/portfolio-mode.md` covers portfolio-level *risk math*, not capital reallocation. "Add X / pull from Y" is net-new modeling: derive per-symphony capital from Composer holdings×prices, or add a capital-weight field. **Single largest modeling unknown** (but per user it's advisory/backtest-agnostic, so no gate proxy needed — only the data model).

---

## 6. Scheduling Primitives

- **Minute scheduler:** `run_scheduler()` (**`app.py:401-407`**) via `schedule` lib (`app.py:21`). Jobs: `threaded_trigger` every min at :00 (spawns `alpha_bot_execution.py` subprocess `:308-323`), `_refresh_account_totals` every min, **`_run_trigger_retention` daily at 02:00 (`:404`) — existing off-hours-job precedent.** New off-hours pipeline adds `schedule.every().day.at("HH:MM").do(...)` here.
- **Autotuner / EOD:** heavy offline work in engine subprocess at EOD, gated Friday/weekend/force (`alpha_bot_execution.py:1109-1130`): `autotuner.run_autotuner(...)`. Advisor producers (OC, Spec Critic, Divergence Explainer) invoked post-walk-forward inside `run_autotuner` (`autotuner.py:2009-2011, 2682-2726`), per-producer isolation. Manual path `POST /api/force_eod` → `force_eod()` (`app.py:1952-2007`).
- **Offline-job-writes-rows-live-path-reads precedent:** `save_regime_label` (`database.py:1174-1199`) "called by the OFFLINE daily job", read live via `get_cached_regime_label`.

**Pipeline hook seams:** (a) a `schedule.every().day.at(...)` job in `run_scheduler()` (cleanest, matches 02:00 retention precedent, runs in Flask daemon process = has Composer-credential context), or (b) append producers to the post-walk-forward block in `autotuner.py:2682-2726` (rides weekly cadence, only Fri/weekend, runs in engine subprocess). **Net-new daily-at-time job is lowest-risk** — primitive already exists.

---

## 7. Data Wall / Advisory Boundary (hard constraints)

- Dashboard is NOT a live-trade-action surface (`.claude/CLAUDE.md:37`, arch #2). Only two guarded write paths, both CSRF + `_SETTINGS_WRITE_ALLOWLIST`. `LIVE_EXECUTION`/credentials excluded.
- `is_live=True` explicit, never default (arch #4); `live_mode` DEFAULT 0.
- Templates open SQLite read-only; UI never reruns engine (arch #5). Advisor reads use `get_ro_connection()`.
- `advisor_observations` structurally non-actionable — `is_advisory_only` hard-wired 1 (`database.py:1068-1069`; migration 017:5-6). "The Advisor never moves money."
- Advise-only apply guidance is plain text, never a button (`ADVISE_ONLY_APPLY_TEMPLATE`, `asset_swap_engine.py:62-65`).
- OFFLINE / no-import rule: every advisor engine forbids import from `alpha_bot_execution.py` (no blocking I/O on 1-min path, arch #1). Lazy imports enforce in routes (`app.py:2881-2887`).
- Chat hard explain-only (`advisor_chat.py:7-16, 226-256`).
- **The data wall:** codebase rejects fabricating analytical context — `_build_volatility_regime` returns `available: False` rather than fake data; CVaR-divergence detector rejected because relocating the validation problem "does not escape the data wall" (`DECISIONS.md:55, 211`). **Every new lens follows honest-availability: mark `available: False` + `reason` when data is thin, never fabricate.**

**Implication for capital allocation:** "accept" cannot execute a capital move. Boundary permits only: persist an advisory observation (`is_advisory_only=1`), surface plain-text apply-guidance, record operator's decision.

---

## Biggest integration unknowns (flagged by research)

1. **Capital-allocation data model (§5)** — no per-symphony capital/weight exists; net-new. (User: advisory/backtest-agnostic, so only the data model is needed, not a gate proxy.)
2. **News-citation provenance** — no fetch/storage/dedup/freshness for external news exists. `external_data/` dir exists (unexamined). Needs structured `sources` convention in `raw_response` + chat artifact allowlist + JS render layer.
3. **Multi-lens data sourcing + data wall (§7)** — each lens needs a source the project lacks; every lens must degrade to `available: False` gracefully. Significant per-lens plumbing. (See DATA-SOURCES.md for the free-first provider map.)
4. **Where the pipeline runs (§6)** — daemon minute-scheduler vs engine subprocess; different processes, different credential/state access.
5. **Suggestion-schema fork (§1)** — Claude config-suggestion path (`ConfigSuggestionsResponse`) vs backtest-gated swap/logic path are two output contracts. Multi-lens swaps + capital moves need a decision: extend the Claude path, the gated path, or a third. Accept/persist/audit wiring differs (`llm_suggestions` vs `advisor_observations`).
