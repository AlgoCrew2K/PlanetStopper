# Gate-1 Acceptance Criteria — Multi-Lens AI Advisor

**Feature:** Expand the existing advise-only AI Advisor into a multi-lens portfolio analyst.
**Scope framing:** SCAFFOLD — full structure end-to-end with honest degradation, NOT deep per-lens tuning.
**Author:** synthesizer (Gate-1 A/C drafting) | **Date:** 2026-06-10
**Grounding:** `EXTENSION-POINTS.md` (Explore agent, opus, 2026-06-10) + `DATA-SOURCES.md` (alpaca-api-researcher, 2026-06-10), each anchor spot-checked against live code (`ai_advisor.py`, `advisors/asset_swap_engine.py`, `advisors/advisor_chat.py`, `database.py`, `app.py`, `templates/ai_advisor.html`, `static/ai_advisor.js`, `external_data/`, `migrations/`).

> **GATE-1 NOTE — this document defines WHAT gets built (user-visible behavior, edge cases, error states, scope boundaries). It deliberately does NOT propose file edits, worker decomposition, or an implementation plan — that is Gate-2 (HOW). The sequencing note in §6 is ordering-only.**

---

## 1. Feature Summary

The existing AI Advisor is a mature, advise-only, OFFLINE subsystem with a hard read-only / data-wall boundary (`is_advisory_only` hard-wired to `1` at `database.py:1090`; no money-moving button anywhere). This feature expands it into a multi-lens portfolio analyst: a 5-lens read-only analytical overlay (technicals, sentiment/news, derivatives-options, macro/economic, fundamentals) attached per-symphony and per-holding, each honoring honest-availability (`available: false` + a reason when free data doesn't cover it — never fabricated). The lenses feed two new suggestion flavors — lens-informed ticker swaps (still gated by the existing BHY-FDR backtest spine) and backtest-AGNOSTIC symphony capital-allocation observations — plus a HARD requirement that every news-resting claim carries a structured, clickable source `{title, url, published, lens}`. All of it surfaces on the existing AI Advisor SPA, is explainable via the existing chat, and is produced by a new scheduled OFF-HOURS pipeline (not live agents) that persists ranked candidates to `advisor_observations` for the dashboard to read. The pipeline ALWAYS produces a **market-prism Overview summary** (a per-lens digest of what the prism saw + an overall market-sentiment read) that the AI Advisor **Overview** tab displays on EVERY run — even when the strict gate yields zero suggestions, so the advisor is never empty.

---

## 2. In-Scope Components

Each component below lists: **Description**, **Testable acceptance criteria** (user-visible), **Key edge cases**, **Error / empty states**. Every code reference was verified against the live tree.

### Component 1 — 5-Lens Analytical Overlay (read-only, per-symphony / per-holding)

**Description.** Five analytical lenses computed offline and attached to the advisor context and to the overlay UI: **technicals**, **sentiment/news**, **derivatives-options**, **macro/economic**, **fundamentals**. Each lens is a self-describing block following the existing honest-availability pattern — the live precedent is `_build_volatility_regime` (`ai_advisor.py:537`), which **currently always returns `available: False`** because its source columns don't exist. New lens blocks are added as new top-level keys in the context dict at `ai_advisor.py:526-544`, each fed by a `_build_<lens>_section(...)` helper mirroring `_build_optuna_section` / `_build_volatility_regime`. The allowlist governance boundary (`ai_advisor.py:472-475`: never reads `dict(os.environ)` / `.env`; nothing un-enumerated enters the dict) MUST hold — lens data is market/analytical context only, never credentials, account IDs, or safety flags.

**Testable acceptance criteria.**
1. Each of the 5 lenses returns a structured block carrying at minimum `{ lens: <name>, available: bool }`; when `available: true` it carries the lens payload, when `available: false` it carries a non-empty human-readable `reason`.
2. A lens whose free source is not yet connected returns `available: false` with a reason naming the missing source (e.g. "FMP fundamentals source not connected") — and this is an ACCEPTING outcome (SCAFFOLD bar), not a failure.
3. Lenses are addressable at two granularities: per-symphony (aggregate) and per-holding (per-ticker). A holding with no lens coverage degrades to `available: false`, never to fabricated values.
4. No lens block contains any value sourced from `os.environ` / `.env` / account credentials (allowlist-boundary assertion; mirrors `ai_advisor.py:472-475`).
5. The overlay renders each lens block on the dashboard with its `available`/`reason` state visible to the operator (see Component 6).

**Key edge cases.**
- A holding ticker that no free provider covers (illiquid / non-US) → `available: false`, reason cites no-coverage.
- Partial coverage: a lens available at the symphony aggregate but not for one constituent holding (or vice versa).
- A free provider returns an empty list (no news, no estimates) vs. an error (timeout/429) — these are distinct states and must not collapse into one.
- Stale data: a lens payload older than its freshness window must be flagged (not silently served as current).

**Error / empty states.**
- Provider error/timeout/429 → `available: false`, reason = a sanitized provider-error class (NEVER raw `str(exc)` — mirror the D-1 contract at `app.py:2948-2951` that returns `type(exc).__name__` only).
- Empty-but-successful fetch (no items) → `available: true` with an empty payload + an explicit "no items in window" marker, distinct from `available: false`.
- Fabrication is a HARD FAIL: any lens emitting a numeric/textual value not traceable to a real fetched source is a Gate-1 reject.

**Per-lens availability at SCAFFOLD (free-first — see `FREE-DATA-SOURCES.md`, 2026-06-10). The exhaustive free sweep flipped most lenses from "degraded" to free-and-live day-one:**

| Lens | Best FREE source(s) | Day-one availability | Notes / uncertainty |
|---|---|---|---|
| Technicals | Alpaca Basic (IEX, already plumbed in `synthetic_history.py`) + Alpha Vantage (server-side indicators, 25/day free) | `available: true` (delayed/EOD) | Realtime full-SIP bars need Alpaca ATP $99/mo — OUT of scope (§3.5); free = IEX-delayed/EOD. Indicators computed operator-side or via Alpha Vantage. |
| Sentiment / News | **GDELT 2.0 DOC API** (free, no key, tone score + clickable `url`+`seendate`, ~15-min) + Alpaca News (plumbed); sentiment scored via existing `anthropic` client | `available: true` | GDELT is the headline free win — broad global news + tone + click-through at $0. Aggregate-score-only endpoints can't back a clickable claim (CC-4). |
| Derivatives / Options | **CBOE put/call** + **OCC volume** (free CSV/XML, no key) + Alpaca indicative IV/greeks (free) | `available: true` for put/call + raw IV/greeks; GEX/skew computed operator-side or `available: false` | Free put/call is the canonical CBOE source. Options history shallow (Alpaca since Feb 2024). FlashAlpha free (5/day) optional for pre-computed GEX. |
| Macro / Economic | **FRED** + **US Treasury XML** (key-less yield curve) + **BLS** + **BEA** | `available: true` | Fully free. FRED/BLS/BEA need a free key; Treasury XML + World Bank need none. Release links satisfy click-through. |
| Fundamentals | **SEC EDGAR `companyfacts`/`companyconcept`/`frames`** (free, no key, UA header, ≤10/sec) | `available: true` | FREE standardized GAAP statements straight from filings — **replaces FMP $22/mo**. Pre-computed health scores (Altman-Z/Piotroski) computed operator-side from the free facts. |
| *(Fundamentals enrichment — free)* | **SEC EDGAR `submissions` + filing RSS/Atom + Full-Text Search** | `available: true` | Free Form 4 (insider) / 13F (institutional) / 8-K (material-event) feed with filing click-through — a corporate-events sub-signal the first survey never covered. Folds into the fundamentals lens + discovery, NOT a new top-level component. |

> **Free-first constraint binding here (UPGRADED by the free sweep):** the entire 5-lens overlay can run at **$0/mo** — GDELT + SEC EDGAR + FRED/Treasury/BLS/BEA + CBOE/OCC + Alpaca-free, with sentiment derived via the existing `anthropic` client. This **replaces the earlier ~$121/mo paid minimal stack**. The only things traded away vs. paid: realtime full-SIP bars (Alpaca ATP $99 — out of scope) and pre-computed health/options aggregates (computed operator-side). A lens whose free source isn't connected yet still degrades honestly to `available: false` — acceptable for SCAFFOLD. Free-registration keys the operator provisions: **FRED, BLS, BEA** (and optionally Alpha Vantage / Finnhub); **SEC EDGAR + GDELT + Treasury + CBOE/OCC need only a User-Agent header / no key.**

---

### Component 2 — Lens-Informed Ticker-Swap Suggestions (backtest-GATED)

**Description.** Extend the existing objective-directed swap engine so a fundamental/sentiment-aware signal influences candidate ranking. The exact seam is `generate_objective_directed_candidates(...)` at `advisors/asset_swap_engine.py:285-423`, which today ranks purely on correlation / variance / pseudo-Sharpe from a `correlation_data` dict (`:341-418`). A lens supplies an extra scoring signal or a new `objective_type` branch here. **These swaps STILL route through the existing BHY/Yekutieli FDR backtest gate** — `backtest_gate_engine.evaluate_candidate_batch` (`backtest_gate_engine.py:463-669`); callers must NOT bypass it by calling `acceptance_gate` directly (`backtest_gate_engine.py:471-474`).

**Testable acceptance criteria.**
1. A lens-informed swap candidate is produced by the same `generate_objective_directed_candidates` seam and carries lens evidence in its metadata (e.g. which lens + which signal moved its rank), distinguishable from a pure-correlation candidate.
2. Every lens-informed swap candidate passes through `evaluate_candidate_batch` and receives a `verdict.decision ∈ {ADOPT_CANDIDATE, KEEP_INCUMBENT, REJECT_VETO_FAILED}` — no swap reaches the operator without a gate verdict.
3. Every survivor carries the mandatory `SURVIVOR_OVERFITTING_CAVEAT` (`backtest_gate_engine.py:108-114, 650-651`).
4. Lens evidence does NOT alter the FDR math — raising the candidate count still raises the BHY bar (`n_effective = len(candidates)`); lens signal affects ranking/generation only, never the gate threshold.
5. A swap proposal carries a structured `sources` list (see Component 4) alongside its existing free-text `objective_rationale`.

**Key edge cases.**
- Lens unavailable for the symphony → swap engine falls back to today's correlation/variance ranking; proposal notes the lens was unavailable.
- Lens signal and correlation signal disagree → both surface in the rationale; the gate verdict, not the lens, decides adoption.
- Zero survivors after the gate → explicit "no survivor" message (existing `run_result.message` / `AC-2.5` precedent at `app.py:2958-2961`).

**Error / empty states.**
- Composer key absent → existing `_has_composer_key()` guard returns "advisor unavailable: API key not configured" (`app.py:2889-2890`).
- NAME→hash resolution failure → fail loudly per existing RC-6 (`app.py:2920-2921`), never silently backtest a display name.
- Engine exception → return `type(exc).__name__` only (D-1, `app.py:2946-2951`).

---

### Component 3 — Symphony ADD-Candidate Suggestions (backtest-AGNOSTIC, advisory-only)

**Description.** A new suggestion flavor: **"consider ADDING symphony X."** The advisor screens candidate symphonies/strategies and surfaces ones worth adding to the portfolio, with lens-informed reasoning + citations. Per **explicit user decision (2026-06-10)**, the advisor **NEVER suggests which symphony to pull money FROM / defund — that is the operator's job and is OUT of scope (§3.10).** The advisor proposes ADDITIONS only; the operator decides the funding source independently. These suggestions are **backtest-AGNOSTIC** — they do NOT route through `backtest_gate_engine` and have no FDR gate proxy. They are advisory observations only: reasoning + citations + plain-text apply-guidance, persisted via `insert_advisor_observation` with `is_advisory_only=1` (hard-wired at `database.py:1068/1089`). The operator executes any add manually in Composer.

**Candidate universe (SCAFFOLD note).** The primary source of add-candidate symphonies is **algo-db.com (a fast-follow lens, OUT of this A/C — §3.1)**. At the scaffold bar, this component stands up its full structure — observation type, persistence, UI panel — and honestly renders "no add-candidates yet — algo-db source connects in fast-follow" until the candidate source is wired. Lens-informed scoring (Components 1/4) is exercised against any candidate that IS available (e.g. a manually-seeded one for the live-render gate).

**Current-allocation display context (NOT a defunding driver).** Per-symphony weight is derived from **Composer holdings × prices** purely as DISPLAY context, so the operator can see current allocation while making their own funding decision. No per-symphony capital/weight representation exists today (verified: `bot_state` holds holdings tickers+amounts keyed by Composer hash at `database.py:249`; account totals from `_refresh_account_totals` at `app.py:343-398`; `symphony_strategies` is risk-engine config, not capital). The advisor does NOT use this weight to recommend defunding.

**Testable acceptance criteria.**
1. An add-candidate suggestion is persisted to `advisor_observations` with a new `advisor_role` (e.g. `ADD_CANDIDATE`), `is_advisory_only=1`, and a `raw_response` JSON carrying: `candidate_symphony` (the proposed addition), lens-informed reasoning, citations (`sources`), and plain-text apply-guidance. It carries NO `pull_from` / defunding field.
2. The suggestion does NOT call `backtest_gate_engine` / `acceptance_gate` and carries no gate verdict (its absence is correct, not a bug).
3. The advisor emits NO suggestion that names a symphony to pull money from / reduce / defund (assertable: no `pull_from` / `defund` / `reduce` semantic in any persisted add-candidate observation).
4. Current per-symphony weight (Composer holdings × prices) is surfaced as display-only context with its as-of timestamp; it never drives a defunding suggestion.
5. "Accept" on an add-candidate records an operator decision + surfaces plain-text apply-guidance ONLY — it NEVER executes a capital move and NEVER renders a money-moving button (Cross-cutting CC-1).
6. The suggestion is explainable via chat (Component 5).

**Key edge cases.**
- No candidate source connected yet (algo-db is fast-follow) → panel honestly shows "no add-candidates yet", never a fabricated suggestion.
- A symphony with zero/empty holdings → display weight derivation yields 0 or `available: false`; no fabricated weight.
- Holdings present but no price for a ticker → display weight is partial; the observation flags the missing-price gap rather than silently undercounting.

**Error / empty states.**
- Composer holdings fetch fails → no display weight; the panel records the source as unavailable rather than emitting a fabricated weight.
- No candidate source / all candidates filtered out → explicit "no add-candidates this run" empty state, never a fabricated addition.

---

### Component 4 — Cited, CLICKABLE News (HARD requirement)

**Description.** Every news-resting claim in the overlay MUST let the operator click through to the source article / market statement. No such markup or fetch/storage exists today — verified: `external_data/` holds only `consensus_prices.csv.gz`, `market_days.csv.gz`, `ticker_metadata.csv.gz` (price/calendar/metadata, NO news), and a grep of `static/ai_advisor.js` for news/url/source/article/href returns only chat-navigation hrefs. Each cited claim carries a structured source object `{title, url, published, lens}`, stored as a first-class `sources` array inside `advisor_observations.raw_response` (JSON — no schema migration required; `raw_response` accepts arbitrary structured payload, `database.py:1058, 1079-1083`).

**Free providers confirmed to return clickable `url` + publish timestamp (from DATA-SOURCES.md):** Alpaca News (`url` + `created_at`, history to 2015, already integrated), Marketaux (`url` + `published_at`, free 100/day), Finnhub company-news (`url` + `datetime`, free 60/min — JS-rendered docs, test against live key), Benzinga direct (hyperlink, free basic tier), FRED (links to Fed/BLS release pages, for macro statements).

**Testable acceptance criteria.**
1. Every news-resting claim surfaced in the overlay carries ≥1 structured source `{title, url, published, lens}` with a non-empty, well-formed `url`.
2. The dashboard renders each source as a clickable `<a href>` (new render helper in `static/ai_advisor.js`, fed by the `sources` array; built via the existing `escHtml`-everywhere pattern at `static/ai_advisor.js:24-31` — XSS-safe).
3. A clicked link opens the source article/statement (Alpaca News → benzinga.com; FRED → Fed/BLS release page).
4. Sources persist round-trip: written into `raw_response.sources`, read back by the dashboard, rendered identically.
5. Aggregate-score-only providers (Finnhub `news-sentiment`, social-sentiment, FMP health scores) are SUPPLEMENTARY signals and may NOT be presented as a clickable news claim — they carry no article URL (DATA-SOURCES.md §News-Citation Provenance).

**Key edge cases.**
- A claim derived from a score-only provider (no URL) → it is NOT presented as a clickable-news claim; it is labeled a derived signal without a click-through.
- Duplicate articles across providers → de-duplicated by URL (no dedup machinery exists today — net-new).
- A `url` that 404s at click time → out of scope to validate live; the stored URL must at minimum be well-formed at persist time.

**Error / empty states.**
- News source unavailable → the lens degrades to `available: false` (Component 1); no fabricated citation.
- A claim that would rest on news but has NO source → the claim is SUPPRESSED (citation-missing = no claim), never shown un-cited. This is the citation-missing pass/fail.

---

### Component 5 — Chat About Any Suggestion / Overlay (explain-only)

**Description.** Reuse the existing explain-only chat (`advisors/advisor_chat.py`). New candidate types (lens-informed swaps, capital-allocation) and the news-citation fields MUST be added to the chat artifact allowlist `CHAT_ARTIFACT_ALLOWED_FIELDS` (`advisor_chat.py:68-121`) or `validate_artifact` (`advisor_chat.py:135-162`) strips them — verified: the current allowlist has NO `sources` / `url` / `title` / `published` / `lens` fields, and unknown fields are silently stripped (`:153-156`). Chat is HARD explain-only (`advisor_chat.py:7-16, 226-256`): no trade directives, no new recommendations, no write path.

**Testable acceptance criteria.**
1. The new candidate types' fields (capital `from_symphony`/`to_symphony`, lens evidence, and the `sources` citation fields) are present in `CHAT_ARTIFACT_ALLOWED_FIELDS` and survive `validate_artifact` round-trip (not stripped).
2. Chat can explain a lens-informed swap, a capital-allocation observation, and a cited news claim — referencing the lens + the source — WITHOUT issuing any new recommendation or trade directive (explain-only invariant holds).
3. A field NOT on the allowlist is still stripped (prompt-injection defense preserved, `:153-156`).
4. Oversized field values remain truncated to `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS` (`:132, 157-159`).

**Key edge cases.**
- A citation `url` longer than the field cap → truncation must not corrupt the click-through; if truncation breaks a URL the URL is dropped, not served broken (flag for Gate-2).
- Nested `sources` array vs. the depth-2 allowlist boundary (`CHAT_ARTIFACT_MAX_DEPTH = 2`, `:127`) — the `sources` list-of-dicts shape must be reconciled with the strip-don't-recurse boundary (flag for Gate-2).

**Error / empty states.**
- Artifact with no allowlisted fields → `validate_artifact` returns `{}` (`:140-141`), chat declines gracefully.
- `ANTHROPIC_API_KEY` absent → chat unavailable, surfaced via `chat_available` (`app.py:2831`).

---

### Component 6 — Dashboard Surface (new tab panel(s) on the existing SPA)

**Description.** Surface the overlay on the existing single unified AI Advisor SPA `templates/ai_advisor.html`, which today renders exactly 5 in-place tab panels (Overview, Correlations, Asset Swaps, Logic Changes, Chat — tab nav at `:556-601`, verified). New candidate types get a new `<div class="tab-panel" data-tab="...">` plus a tab button, with the panel's data assembled server-side in `ai_advisor_tab()` (`app.py:2735-2845`) and any evaluate action following the asset-swaps route template (NAME→hash resolution at `app.py:2911-2921`). Card rendering extends `renderSuggestions` (`static/ai_advisor.js:84+`), reusing `escHtml` and accept/reject inline wiring.

**Testable acceptance criteria.**
1. The new tab panel(s) render on `/ai-advisor` alongside the existing 5 tabs, switchable in-place via the existing JS tab switcher (no page navigation).
2. The overlay panel displays, per symphony/holding, each lens's `available`/`reason` state and (when available) its payload, plus clickable citations (Component 4).
3. The panel surfaces lens-informed swaps (with gate verdict) and capital-allocation observations (without gate verdict, with apply-guidance).
4. Accept/reject reuse the existing inline wiring; the dashboard remains read-only (templates open SQLite read-only, arch #5) and writes go only through CSRF-protected POST routes.
5. The page renders correctly with real data in a live browser (PM-owned live-render gate — necessary-but-not-sufficient green unit suite; the final reviewer APPROVE is conditional on this gate per project memory `feedback_reviewer_approve_conditional_on_live_gate`).

**Key edge cases.**
- A symphony with all 5 lenses `available: false` → the panel shows 5 honest-unavailable blocks, not an empty/blank panel.
- One combined overlay tab vs. two separate tabs (lenses vs. capital-allocation) is an OPEN QUESTION for Gate-2 (§5).

**Error / empty states.**
- No observations persisted yet (pipeline hasn't run) → the panel shows an explicit "no analysis yet — pipeline runs off-hours" empty state, not a spinner-forever or a blank.
- Composer key absent → existing `no_api_key` path (`app.py:2809-2814`).

---

### Component 7 — Scheduled OFF-HOURS Pipeline (multi-pass; NOT live agents)

**Description.** A daily scheduled OFF-HOURS job that runs a multi-pass pipeline — **one reasoning pass per lens → a news/citation pass → a synthesis pass → ranked candidates persisted to `advisor_observations`**. The dashboard reads what the pipeline persisted (offline-writes / live-reads precedent: `save_regime_label` at `database.py:1174-1199`, written by the offline daily job, read live via `get_cached_regime_label`). The scheduling primitive already exists: `run_scheduler()` at `app.py:401-407` hosts a daily 02:00 retention job (`_run_trigger_retention`, `app.py:404`) — a net-new `schedule.every().day.at("HH:MM").do(...)` is the lowest-risk hook (runs in the Flask daemon process, which has Composer-credential context). These are NOT live agents and are NEVER imported from `alpha_bot_execution.py` (Cross-cutting CC-2).

**Testable acceptance criteria.**
1. The pipeline is registered as a daily off-hours scheduled job (the exact time is an OPEN QUESTION, §5) and runs the 4 passes in order.
2. Each per-lens pass writes its lens block; the news/citation pass attaches structured `sources`; the synthesis pass produces ranked candidates AND ALWAYS writes the market-prism Overview summary (Component 8) even when zero candidates survive; all persist to `advisor_observations` with `is_advisory_only=1`.
3. Candidates persist REGARDLESS of verdict (RC-4 precedent: `asset_swap_engine._persist_observation` persists regardless of verdict; capital candidates have no verdict at all).
4. A lens pass that fails (provider down) does NOT abort the pipeline — it records that lens `available: false` and the pipeline continues (per-producer isolation precedent, EXTENSION-POINTS.md §6).
5. NO advisor engine / lens module is importable from `alpha_bot_execution.py` (the 1-minute execution path) — assertable by an import-boundary test (CC-2).
6. The pipeline writes nothing to the live-trade path and moves no money (CC-1).

**Key edge cases.**
- Pipeline run overlaps market hours / a prior run still executing → must not block the minute scheduler (`threaded_trigger` / `_refresh_account_totals` run every minute, `app.py:401-407`); off-hours timing + non-blocking design required.
- Partial pipeline failure mid-pass → already-computed lens blocks persist; the synthesis pass works with what's available (honest partial).
- Number of candidates per run is an OPEN QUESTION (§5).

**Error / empty states.**
- All lenses unavailable on a run → pipeline persists an honest "no analysis available this run" observation, not an empty or fabricated one.
- Provider rate-limit (429) → that lens degrades to `available: false` with a rate-limit reason; pipeline continues.

---

### Component 8 — Always-On Overview "Market Prism" Summary (independent of suggestions)

**Description.** The AI Advisor **Overview** tab MUST ALWAYS display the latest overnight analysis — a synthesized digest of everything the pipeline saw across all lenses, plus an overall market-sentiment read ("the prism") — **EVEN on runs that produce zero suggestions.** This is a HARD user requirement (2026-06-10): the strict CRRA-EU / Harvey-Liu FDR gate means suggestion lists are often empty *by design* (see project memory `project-advisor-does-nothing-three-layer-rootcause` — empty suggestions are correct, not a failure), but the advisor must never look "dead." The Overview shows the market summary + aggregate sentiment regardless; suggestions live in their own tabs and are explicitly NOT required in Overview. The synthesis pass of the pipeline (Component 7) ALWAYS writes a market-prism summary observation that the Overview renders.

**Testable acceptance criteria.**
1. Every overnight pipeline run persists exactly one market-prism summary observation (e.g. `advisor_role = MARKET_PRISM`, `subject_type = portfolio`, `is_advisory_only=1`) containing: a per-lens digest (what each lens saw + its `available` state), an overall market-sentiment read (e.g. risk-on / neutral / risk-off with a short rationale), and the run timestamp.
2. The Overview tab ALWAYS renders the latest market-prism summary, INCLUDING on runs where every suggestion tab (swaps, add-candidates, config) is empty. An empty suggestion list NEVER produces an empty Overview.
3. The summary distinguishes per-lens availability — a lens that was `available: false` is shown as "no data" in the digest, not omitted and not fabricated.
4. The overall sentiment is synthesized OVER the available lenses only; if most lenses are unavailable, the read honestly states low confidence / limited inputs rather than overstating (data-wall, CC-3).
5. Every news-resting statement in the summary carries clickable citations (Component 4 / CC-4).
6. The summary is timestamped and labeled with the run it came from; a stale summary (pipeline hasn't run since) is shown with its as-of time, never presented as current.

**Key edge cases.**
- First run / no prior summary → Overview shows "no analysis yet — first overnight run pending," not a blank.
- All lenses unavailable on a run → an honest "limited market view this run — inputs unavailable" summary, never a fabricated sentiment.
- Pipeline failed mid-run → the last successful summary remains visible with its as-of time + a "latest run incomplete" marker.

**Error / empty states.**
- Pipeline never ran → explicit first-run empty state (as above), never a spinner-forever or blank panel.
- Summary generation itself errors → Overview shows the prior summary + a non-fatal "latest run incomplete" note; the error is logged class-only (D-1), never surfaced raw.

---

## 3. Explicit OUT-OF-SCOPE

The following are explicitly NOT in this A/C:

1. **algo-db.com (6th MongoDB-Atlas lens)** — FAST-FOLLOW. Out of this A/C entirely; documented only as a future extension point (EXTENSION-POINTS.md §1 reserves an `algo_db` context key for it).
2. **Alpaca auto-execution / any automated trade or capital move** — the advisor never moves money; "accept" records an operator decision only.
3. **Any money-moving button or live-trade-action surface on the dashboard** — the dashboard is NOT a trade surface (arch #2).
4. **Deep per-lens tuning / modeling sophistication** — SCAFFOLD bar is structure + honest degradation, not tuned signals. A lens returning `available: false` with a correct reason is acceptable.
5. **Paid data providers / new paid subscriptions** — free-first only, and the exhaustive sweep (`FREE-DATA-SOURCES.md`) confirms all 5 lenses are coverable at $0 (GDELT + SEC EDGAR + FRED/Treasury/BLS/BEA + CBOE/OCC + Alpaca-free + `anthropic`). Alpaca ATP ($99/mo realtime SIP), FMP paid tiers, Marketaux paid, FlashAlpha paid, ORATS, Polygon/Massive, Finnhub premium are all OUT. Realtime full-SIP bars and pre-computed health/options aggregates are the only things the free stack trades away (computed operator-side or deferred).
6. **Backtest/FDR gating of capital-allocation candidates** — explicitly backtest-agnostic per user decision.
7. **Live URL-reachability validation of citations** — stored URLs must be well-formed at persist time; verifying they resolve at click time is out of scope.
8. **News dedup/freshness machinery beyond URL-level dedup at persist** — minimal dedup only for SCAFFOLD.
9. **A capital-move execution audit/rollback** — there is no execution, so nothing to audit beyond the advisory observation.
10. **Suggesting which symphony to pull money from / any defunding or capital-reduction recommendation** — explicitly operator-owned per user decision (2026-06-10). The advisor proposes ADDITIONS only; it never names a symphony to defund or reduce. Current per-symphony weight is surfaced as display context only, never as a defunding driver.

---

## 4. Cross-Cutting Acceptance Criteria (hard constraints as pass/fail)

| # | Constraint | Pass/fail check | Anchor |
|---|---|---|---|
| CC-1 | **Advise-only / never moves money** | Every persisted observation has `is_advisory_only=1`; "accept" on ANY candidate (swap, capital, lens) records an operator decision + plain-text apply-guidance only, with NO money-moving button anywhere in the new UI. | `database.py:1090` (hard-wired); `asset_swap_engine.py:62-65` (plain-text apply guidance); `.claude/CLAUDE.md` arch #2 |
| CC-2 | **No blocking I/O on the 1-minute execution path** | No lens/advisor/pipeline module is importable from `alpha_bot_execution.py`; an import-boundary test proves zero such import. Routes use lazy imports (`app.py:2881-2887`). | EXTENSION-POINTS.md §2/§7; arch #1 |
| CC-3 | **Data wall — never fabricate** | Every lens degrades to `available: false` + a non-empty reason when data is thin; NO lens emits a value not traceable to a real fetched source. Mirror of `_build_volatility_regime` (`ai_advisor.py:537`, always `available: False` today). | EXTENSION-POINTS.md §7; `DECISIONS.md:55, 211` |
| CC-4 | **Citation-missing = no claim** | A news-resting claim with no structured `{title,url,published,lens}` source is SUPPRESSED, never shown un-cited. Score-only providers cannot back a clickable claim. | DATA-SOURCES.md §News-Citation Provenance |
| CC-5 | **Free-first only** | The build introduces NO paid subscription; lenses use FRED (free) + FMP free tier + Alpaca free + `anthropic` client. Unconnected free sources degrade honestly. | DATA-SOURCES.md §Recommended Minimal Stack; this A/C §3.5 |
| CC-6 | **Two-DB pattern** | No cross-join between the state DB and the optimization DB in app code; needed rows are copied. Advisor writes go to the state DB `advisor_observations`. | arch #3 |
| CC-7 | **Read-only dashboard** | Templates open SQLite read-only; advisor reads use `get_ro_connection()`; the UI never reruns the engine. | arch #5; `database.py:1111` |
| CC-8 | **CSRF on writes** | Every new POST route is CSRF-protected (mirror existing `/ai-advisor/*` POSTs). | EXTENSION-POINTS.md §4; arch #2 |
| CC-9 | **Composer NAME→hash on Composer-calling routes** | Any new route that calls a Composer endpoint resolves NAME→hash via `bot_state` and fails loudly if unresolved (never silently passes a display name → HTTP 400/empty). | `app.py:2911-2921` (reusable pattern); project memory `feedback_diagnose_before_explaining` |
| CC-10 | **D-1 error contract** | New routes return `type(exc).__name__` only on exception; never echo raw `str(exc)` (may leak keys/paths). | `app.py:2948-2951` |
| CC-11 | **Allowlist governance preserved** | The advisor context never introduces credentials, account IDs, or safety flags via a lens; the `os.environ`/`.env`-exclusion boundary holds. | `ai_advisor.py:472-475` |
| CC-12 | **Chat allowlist updated** | New candidate-type + citation fields are added to `CHAT_ARTIFACT_ALLOWED_FIELDS`; unknown fields still stripped; explain-only invariant holds. | `advisor_chat.py:68-121, 135-162` |
| CC-13 | **Doc-writer in cycle** | A `doc-gen` doc-writer lands the new components' docs (decisions, behavior, honest-availability contract) into `docs/generated/` / `DECISIONS.md` / README and commits BEFORE cycle-complete (global hard rule). | `~/.claude/CLAUDE.md` §Agent Teams |

---

## 5. Open Questions / Assumptions for Gate-2 (flag, don't decide)

These are surfaced for Gate-2 (HOW) — NOT decided here.

1. **One combined overlay tab vs. two tabs.** Lenses + capital-allocation in a single new tab, or separate tabs (lens overlay distinct from capital-allocation)? Affects template/JS layout (Component 6).
2. **Display-weight data model — derived-on-read (assumed) vs. cached.** Per-symphony weight is now DISPLAY-ONLY context (the advisor suggests additions only, never defunding), so the modeling stakes dropped sharply. Assumption for SCAFFOLD: derive from Composer holdings × prices on read, store in the observation `raw_response` JSON (no schema change). Confirm at Gate-2 whether any cached/queryable weight column is needed.
3. **Pipeline cadence / time-of-day.** Daily off-hours — but what time? (02:00 retention is the precedent.) Does it ride the daemon minute-scheduler (`app.py:401-407`, has credential context) or append to the weekly post-walk-forward block (`autotuner.py:2682-2726`, Fri/weekend only)? EXTENSION-POINTS.md §6 favors the net-new daily job as lowest-risk.
4. **Candidates per run.** How many ranked candidates does a single pipeline run persist (per symphony? globally?)? Logic engine has `MAX_SUGGESTED_CANDIDATES = 30` (`logic_change_engine.py:93`) as one precedent.
5. **Suggestion-schema fork.** New lens-informed swaps + capital moves — extend the Claude config-suggestion path (`ConfigSuggestionsResponse`, `llm_suggestions` audit), the backtest-gated path (`advisor_observations`), or a third? Swaps clearly ride the gated path; capital is advisory-only `advisor_observations`; the lens overlay context is the Claude path. Confirm the three-way mapping at Gate-2. (EXTENSION-POINTS.md §1 "Suggestion-schema fork".)
6. **Sentiment derivation cost.** Deriving sentiment via the existing `anthropic` client per-holding per-run has a token-cost / latency profile — acceptable cadence and batching is a Gate-2 detail.
7. **`sources` array vs. chat depth-2 allowlist.** The `sources` list-of-dicts shape must reconcile with `validate_artifact`'s strip-don't-recurse depth-2 boundary (`advisor_chat.py:127`) so citations survive into chat without being stripped or truncated mid-URL (Component 5 edge case).
8. **Suggestible-allowlist count discrepancy.** EXTENSION-POINTS.md §1 says the suggestible surface is a "7-item allowlist"; the live code docstring at `ai_advisor.py:472` says "9-item curated ALLOWLIST." This does not block this feature (lenses add CONTEXT, not suggestible knobs) but the discrepancy should be reconciled by whoever touches `_build_suggestible_surface` — flagged so it isn't laundered into an assertion.
9. **Per-lens "live vs. degraded" target for SCAFFOLD.** The free sweep makes a strong day-one bar feasible: fundamentals (SEC EDGAR, no key), news+tone (GDELT, no key), macro (FRED/Treasury), and put/call (CBOE/OCC, no key) can ALL be `available: true` at cycle-complete — the no-key sources especially. Assumption: demonstrate `available: true` for the no-key free sources (GDELT, SEC EDGAR, Treasury/CBOE/OCC) plus any keyed source the operator provisions (FRED/BLS/BEA); a lens still degrades honestly if its key isn't yet provided. Confirm the exact demonstration bar at Gate-2.

---

## 6. High-Level Sequencing Note (ordering only — NOT an implementation plan)

Natural sub-deliverables the PM can stage as separate teams/cycles, in dependency order. **This is ordering guidance only; it proposes no file edits and no worker decomposition.**

1. **Foundation: lens context contract + honest-availability scaffold.** The 5-lens block shape (`available`/`reason`/payload) + the context-assembly seam + the data-wall degradation. Everything downstream consumes this contract. (Components 1, CC-3, CC-11.)
2. **Citation provenance + chat allowlist.** The structured `sources` convention in `raw_response`, plus extending the chat artifact allowlist so citations and new candidate types survive. (Components 4, 5; CC-4, CC-12.) — Foundational because both swaps and capital candidates carry citations.
3. **Add-candidate observations + display-only weight context.** Persist backtest-agnostic ADD-candidate suggestions (additions only — never defunding); derive per-symphony weight from Composer holdings × prices for display context only. (Component 3.) — Depends on (1)+(2); isolatable. The candidate universe largely depends on the algo-db fast-follow.
4. **Lens-informed swaps through the existing gate.** Extend `generate_objective_directed_candidates`; preserve the BHY-FDR spine. (Component 2.) — Depends on (1)+(2).
5. **Scheduled off-hours pipeline + always-on Market Prism summary.** Wire the 4-pass daily job that produces and persists everything above, including the always-emit market-prism Overview summary. (Components 7, 8.) — Depends on (1)-(4) existing to orchestrate.
6. **Dashboard surface.** New tab panel(s), the always-on Overview market-prism summary, lens overlay rendering, clickable citations, accept/reject reuse, live-render gate. (Components 6, 8.) — Last; consumes persisted pipeline output.

> Each sub-deliverable that writes new codepaths is a Toxic-Pair TDD team (per project CLAUDE.md); doc-only / config-only steps are not. Composition selection and worker decomposition are Gate-2 decisions, not made here.

---

**End of Gate-1 A/C.** Every component reference above was verified against a live `file:line` in this repo. Provider price/feature uncertainties from DATA-SOURCES.md are carried verbatim into §2 and §3.5 — no lens is asserted "live" unless its free source is confirmed connected.
