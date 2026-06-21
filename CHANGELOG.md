# Changelog

## AI Council / Market Prism

### What the AI Council Is

The AI Council is a nightly multi-agent advisory system that produces a "Market Prism" — a structured overnight market read comprising a directional verdict and a per-lens digest across five analytical domains. Each morning the dashboard's AI Advisor Overview tab shows the most recent council report: an overall sentiment chip (risk-on / risk-off / neutral), a rationale paragraph, per-lens summaries (technicals, sentiment, macro, derivatives, fundamentals), and cited sources.

**The council is advisory-only.** It does not affect Guard Alpha trailing-stop logic, autotuner parameters, or any trade or exit decision. It is purely an informational read for the operator.

---

### The Data Lenses

Each nightly council run draws on five independently-maintained data producers. Every producer follows a "honest-availability" contract: when its data source is unreachable or returns stale/incomplete data, it reports `available=False` with a type-only reason rather than fabricating values or crashing the pipeline. A proxy-universe floor on the technicals and fundamentals lenses ensures those lenses always receive a real basket of tickers to analyse even when the portfolio is flat.

#### Technicals Lens (PR #34, 2026-06-13)

Pulls 270 days of daily bar history from Alpaca via the existing `synthetic_history` cache. Computes 50-day and 200-day simple moving-average posture per ticker, market breadth (fraction of the universe above the 50-day SMA, excluding tickers with insufficient history), and 20-day price momentum (Jegadeesh and Titman 1993). A named `_PROXY_UNIVERSE` floor (SPY, QQQ, IWM, EFA, AGG, GLD plus four sector ETFs) guarantees a real breadth reading during off-hours when live holdings are empty. All window constants are named with source comments; a golden-fixture test verifies every indicator computation.

#### Sentiment / News Lens

The sentiment lens was built in two stages.

**Stage 1 — GDELT tone producer (PR #33, 2026-06-13):** Fetched GDELT 2.0 `timelinetone` aggregate AvgTone as a directional sentiment scalar. Bounded 429 retry (base 20 s, max 4 attempts, cap 60 s). Keyless (public API). English-language article filter.

**Stage 2 — Multi-source news corpus (PR #48, 2026-06-18):** Rebuilt the lens into two independent facets. Facet A keeps the GDELT tone scalar as an always-valid floor: even when every article feed fails, the tone signal is still emitted. Facet B adds a ranked, deduplicated, topic-tagged article corpus drawing from GDELT artlist plus eight additional free RSS/Atom feeds (CNBC, MarketWatch, Yahoo Finance, Federal Reserve, BLS, BEA, SEC 8-K atom, and Google News). Articles are scored by named-constant weights for recency (`W_RECENCY=0.40`, decay constant `TAU_HOURS=24`), market-keyword relevance (`W_RELEVANCE=0.35`), and source authority (`W_AUTHORITY=0.25`). A `SOURCE_AUTHORITY` table assigns scores from 1.0 for government sources down to 0.4 for unknown domains. Three-step deduplication removes duplicate URLs, title near-duplicates (Jaccard similarity threshold 0.85), and per-domain excess. The top 25 articles are kept. Each article is topic-tagged (macro, fundamentals, technicals, derivatives, or broad-sentiment) for potential cross-lens routing. Requires `feedparser>=6.0`.

#### Derivatives Lens (PR #30, reverted #29, rebuilt; freshness fix PR #37, 2026-06-16)

Uses FRED VIXCLS (spot VIX) and VXVCLS (3-month VIX) as a derivatives proxy, since no free real-time options data source was identified during research. Classifies a volatility regime and derives a risk read from the term structure.

A significant defect was discovered post-ship and fixed in PR #37: the original producer fetched observations starting from a hardcoded date of 2020-01-01 in ascending order, then selected the "latest" observation from that window — which was actually the oldest end of a 2020 batch, producing a VIX value roughly six years stale while reporting `available=True`. The fix replaced the hardcoded window with a rolling 90-day lookback, and added a staleness guard: if the most-recently-published observation is more than `_OPTIONS_PROXY_MAX_STALENESS_DAYS` calendar days old (approximately 10 days, set above the longest normal market closure), the producer reports `available=False` with `reason="stale_data"` rather than serving the stale value as current. The `as_of_date` field now always reflects the true date of the selected observation.

#### Macro Lens

Pulls multiple FRED series (10-year Treasury yield, unemployment rate, CPI, and the Fed Funds effective rate) via the `FRED_API_KEY` credential. Returns value, date, and a clickable source URL per series. Reports `available=False` with a registration prompt when the API key is absent.

#### Fundamentals Lens (fan-out fix PR #38, 2026-06-17; vintage fix PR #43, 2026-06-17)

Fetches SEC EDGAR companyfacts for a basket of company tickers and extracts key XBRL facts (revenue, net income, total assets, total liabilities, stockholders equity). No API key is required (SEC is public with a descriptive `User-Agent`).

Two defects were discovered and fixed:

**Dead-lens defect (PR #38):** Both production callers invoked `_build_fundamentals_section()` with no ticker argument. The function short-circuited immediately with `available=False, reason="ticker symbol required"` — meaning the fundamentals lens was permanently unavailable in production. The fix adds an internal fan-out path: when called with no ticker, the function derives a universe from live portfolio holdings unioned with a named `_FUNDAMENTALS_PROXY_UNIVERSE` floor of eight large-cap company tickers (not ETFs, which lack companyfacts), then calls the single-ticker SEC helper for each, aggregating results. Per-ticker failures degrade gracefully without killing the whole lens. The single-ticker call signature is preserved byte-for-byte. The proxy floor ensures the lens is always available even when the portfolio is flat.

**Wrong-vintage defect (PR #43):** Two concurrent selection bugs caused the lens to serve financial data one to six years stale even when reporting `available=True`. Mode A: `_SEC_KEY_CONCEPTS` hardcoded a single GAAP tag per concept with no fallback; when an issuer migrated a concept to a new tag (e.g. MSFT migrating `Revenues` through two successor tags to `RevenueFromContractWithCustomerExcludingAssessedTax`), the producer queried only the frozen legacy tag. Mode B: the entry-selection sort key was the `filed` date rather than the reporting-period `end` date; a 10-K bundling prior-period comparative entries caused Python's stable sort to return the oldest entry first. Both bugs were fixed together: `_SEC_KEY_CONCEPTS` was restructured to carry an ordered tuple of candidate GAAP tags per concept (newest naming first), and the selection sort was changed to `end` descending with `filed` as a tiebreak. The output `key_facts` shape is unchanged so downstream consumers are unaffected.

#### Lens Data Warehouse (PR #35, 2026-06-13)

A separate append-only SQLite database (`alphabot_warehouse.db`) — distinct from the state DB and optimization DB — stores every nightly lens data pull for accumulation of proprietary historical data at zero cost. Each snapshot records the lens name, symbol, fetch timestamp, source, availability flag, and the raw payload with API credentials recursively stripped. The warehouse is designed engine-agnostically (raw JSON) so payloads can be migrated to DuckDB or Parquet if volume warrants it. The GDELT sentiment and FRED macro lenses write to the warehouse after each fetch. A pytest sentinel blocks tests from opening the real warehouse file.

---

### The Council (Multi-Agent Architecture)

#### Audit-Log Foundation (PR that included migration 032, merged ~2026-06-13)

Before the council could run, a full deliberation trail had to be storable. Migration 032 added the `prism_audit_log` table to the state DB: `id`, `run_id`, `agent_role`, `phase`, `content`, `created_at`. Two database accessors were added — `insert_prism_audit_entry` and `get_prism_audit_for_run` — and an agent-callable CLI writer (`python -m advisors.prism_audit_write --run-id <id> --role <role> --phase <phase>`, content from STDIN) was added so agent team members can persist their outputs from any working directory. The CLI follows the D-1 error contract: errors emit only `type(exc).__name__` to stderr with a non-zero exit, never a traceback.

A latent bug was fixed post-council-proof-run (PR #46, 2026-06-17): `prism_audit_write.py` did not call `load_dotenv()`, so CLI invocations from a working directory other than the repo root resolved `DB_PATH` from the shell environment rather than the `.env` file, silently writing audit rows to the wrong database. The fix adds `load_dotenv(find_dotenv(usecwd=True))` at module import.

#### The Six Council Agents

The council comprises five lens analysts and one synthesizer, all running on Claude Opus:

- **Technicals analyst** — reasons about moving-average posture, breadth, and momentum
- **Sentiment analyst** — reasons about GDELT tone and the multi-source news corpus
- **Derivatives analyst** — reasons about VIX level, term structure, and vol regime
- **Macro analyst** — reasons about FRED series (yields, unemployment, inflation, Fed Funds)
- **Fundamentals analyst** — reasons about SEC EDGAR companyfacts for the portfolio universe
- **Synthesizer** (team lead) — integrates the analysts' views into the final `MARKET_PRISM` row

Each analyst begins an `initial_read` immediately on session start. Clarifying questions flow freely between analysts at any time and do not count as debate. The synthesizer opens a debate round only when there is genuine disagreement between analysts; debate is capped at three rounds. All phases — initial reads, clarifications, debate rounds, and synthesis — are written to `prism_audit_log` via the CLI writer, creating a fully auditable deliberation trail keyed to the `run_id`.

The synthesizer writes exactly one `MARKET_PRISM` `advisor_observations` row per run and never synthesizes until at least the expected set of `initial_read` audit rows is confirmed present in the database for that `run_id`.

#### Council Reliability Hardening (PR #49, 2026-06-18)

The initial council implementation had reliability problems: analyst agents waited for a synthesizer kickoff message (causing them to sit dormant), the synthesizer addressed agents by canonical name rather than the runtime agentId (causing misdirected messages), and a successful scheduler exit code was treated as a successful run even when no `MARKET_PRISM` row was written. Five orchestration directives were implemented to close these gaps:

1. The `run_id` is generated by `prism_scheduler.py` in `main()` and threaded into every agent's spawn prompt — the council never mints its own run_id.
2. Each analyst's spawn prompt includes the run_id and an instruction to begin the `initial_read` immediately, without waiting for a kickoff message.
3. The primary spawns all six agents and captures their agentIds, then passes those IDs to the synthesizer so it can address each analyst directly.
4. The synthesizer waits until `initial_read` rows are confirmed in the audit DB for all expected analysts before proceeding to synthesis (the "audit-DB wait-barrier" hard rule).
5. The synthesizer never falsely attributes a spawned-but-silent analyst as having produced output.

**F-4 row-verification:** A run is only considered successful if the scheduler exits zero AND `_get_market_prism_row_for_run(run_id)` confirms a `MARKET_PRISM` row exists in the database for that run_id. An exit-zero-without-row is treated as a failed attempt and retried up to `MAX_ATTEMPTS=3` with exponential backoff (base 30 s, cap 60 s). After exhaustion the scheduler exits non-zero loudly. This eliminates silent false-green runs.

The proof run of the hardened council (run_id `637c719f`, 2026-06-18) achieved 5/5 analysts filing audit rows and a real integrated synthesis, at a cost of $5.84 against the metered API (approximately $0 incremental against the Claude subscription).

---

### Dashboard Rendering

#### Overview Tab — Market Prism Block

The AI Advisor Overview tab includes an always-on Market Prism block. When a `MARKET_PRISM` row exists, it renders a sentiment chip (risk-on / risk-off / neutral, semantic CSS class), a rationale paragraph, per-lens availability flags and digests, and a list of cited sources. When no row exists, it renders an informative empty state rather than a blank section.

The sentiment chip color was initially mapped incorrectly: the verdict-to-CSS-modifier logic defaulted known verdicts to neutral styling, so a "bullish" verdict displayed with neutral-gray styling. This was fixed in PR #46 (2026-06-17) — `bullish` and `risk-on` now map to `prism-sentiment-chip--risk-on`, `bearish` and `risk-off` to `prism-sentiment-chip--risk-off`, and anything else to `--neutral`.

#### RF-1 — Prose Render Guard (PR #50, 2026-06-18)

A post-closeout verification found that the per-lens digest cards on the Overview rendered raw JSON when the `lens_pipeline` programmatic path was the producer (e.g. `{"ma_posture": {...}, "breadth": 0.7, "momentum": {...}}`). Council-produced rows already contained readable prose and rendered correctly.

A producer-agnostic render-layer guard was added (`advisors/prism_render.py`). It detects structured JSON digests via `json.loads` and dispatches to per-lens humanizers that produce readable text (e.g. breadth percentage, VIX level and regime, FRED series values, SEC coverage count). Council prose passes through unchanged. Null or unavailable lenses produce an honest "limited inputs — data unavailable" empty state. The guard never raises; it degrades to the empty state on any malformed input. Raw JSON is never exposed on the rendered page.

A second render fix (R2) ensures the observation preview column in the Overview shows the verdict text for `MARKET_PRISM` rows and a humanized preview for other observation types, rather than a raw JSON dump.

---

### Going Live (Production Deployment)

#### Nightly Scheduling (PR #49 `prism_scheduler.py`, guard PR #59, sub-auth PR #60)

The council runs as a standalone Python script (`prism_scheduler.py`) invoked by the operating system's job scheduler — not by the Flask daemon. This design was chosen because an Agent Team is a Claude Code construct; the Flask daemon cannot spawn one. A fresh council session per nightly run avoids marathon-session state accumulation.

The scheduler runs nightly at 03:00 America/New_York via a systemd oneshot service and timer (`Persistent=true` so a missed run fires on next system start). The daemon's own 03:00 lens-pipeline slot — which previously wrote a competing `MARKET_PRISM` row — is silenced when `DISABLE_DAEMON_LENS_PIPELINE=1` is set in the environment (PR #59). The safe transition order is: set the flag and restart the daemon first, then register the council timer. Running both concurrently would produce two `MARKET_PRISM` rows per night with no mutual idempotency guard.

#### Subscription Billing (PR #60, 2026-06-19)

By default, the headless `claude -p` subprocess inherits `ANTHROPIC_API_KEY` from the process environment and bills against the metered API even when a Claude subscription token is available. The scheduler now strips `ANTHROPIC_API_KEY` from the subprocess environment so the council falls back to `CLAUDE_CODE_OAUTH_TOKEN`, billing against the operator's Claude subscription rather than the metered API. The on-demand Flask advisor routes (which call the Anthropic SDK directly, not via a subprocess) are unaffected and continue to use the API key.

#### Deployment Architecture

The production deployment runs on a Linux VPS with the Flask daemon and nightly council operating as a non-root service user from `/opt/planetstopper`. A Caddy reverse proxy handles TLS termination with a Let's Encrypt certificate; the Flask app binds to `localhost:8090` only, and the cloud firewall restricts external inbound access to ports 22, 80, and 443. The deployment runbook is documented in `docs/DEPLOYMENT.md`.

`LIVE_EXECUTION='False'` is set permanently on the droplet. The droplet operates in shadow/advisory mode only — no live trading.

A password-authentication gate (PR #55, 2026-06-19) was added to the dashboard before the public deployment. Unauthenticated browser requests redirect to a login page; unauthenticated API and XHR requests return 401 JSON. The gate is fail-closed: a missing `DASHBOARD_PASSWORD` or `SECRET_KEY` causes the server to refuse all requests rather than serve open. The gate was deployed with constant-time password comparison, proxy-aware failed-attempt throttling (`TRUST_PROXY` gates XFF keying), and proper CSRF token handling for the login form (the CSRF token is read from the form field for form-encoded submissions, from the `X-CSRF-Token` header for all other content types, to preserve the CSRF-before-body-size guard ordering on JSON endpoints).

After the live droplet deployment, the database was wiped clean to begin accumulating production lens snapshots, audit trails, and Market Prism rows from a known-good baseline.

---

### Known Scope Boundaries

- **Advisory-only throughout.** The council, all lens producers, and the Overview rendering path are entirely off the execution path. None of them read or write `LIVE_EXECUTION`, interact with trade orders, or touch the core Guard Alpha engine.
- **`LIVE_EXECUTION` is permanently `False` on the production droplet.** No live trading is performed there.
- **GDELT reachability is environment-dependent.** GDELT operates correctly on the production Linux VPS; it may be intermittently unreachable from isolated development environments. The honest-availability contract means an unreachable GDELT degrades the sentiment lens gracefully rather than failing the council.
- **The council regime hook is not wired to the risk engine.** The council's derived regime classification (risk-on / risk-off) is surfaced on the Overview for operator information only; it is not consumed by the trailing-stop or autotuner logic.
- **A latent display bug exists for `run_ts`.** After the run_id unification in PR #49, the `MARKET_PRISM` observation's `run_ts` field holds the UUID run_id rather than an ISO timestamp. The Overview tab displays `run_ts`, so the "As of" datetime shown for council-produced rows is a UUID. This is a display-only issue; the `created_at` column is unaffected. It is tracked for a future render fix.

---

## Real Opus-Driven Strategy Builder (PR #63, 2026-06-20)

### What Changed

The Strategy Builder's fixed seven-template generator (T1-T7) has been replaced with a real Claude Opus-driven builder. Previously the builder stamped out seven preset strategy shapes regardless of your objective. Now it asks Opus to design strategies from scratch, proposes its own tickers based on market knowledge, validates those tickers against a live universe of every tradeable US-equity on Alpaca, compiles the designs into valid Composer trees, and runs all candidates through the same anti-overfit cull the autotuner uses before surfacing survivors.

The builder is also dual-mode: alongside net-new Opus designs it now pulls existing strategies from the Atlas community database and ranks them by how well they fit your chosen objective. Both sources flow through the same backtest and FDR gate, and every result is tagged with its provenance (built-new or atlas-suggested) so you can tell where each proposal came from.

**The builder is advisory-only.** It does not affect Guard Alpha, it does not touch `LIVE_EXECUTION`, and it does not auto-deploy anything. It proposes strategies for operator review.

---

### Four Objectives

The builder now supports four objectives (up from three):

- **diversify** — strategies with low correlation to your existing holdings
- **cut_drawdown** — strategies with the lowest historical drawdown
- **lift_risk_adjusted** — strategies with the best OOS Sharpe ratio
- **volatility_mitigation** (new) — strategies using inverse-vol weighting or low-vol filters

---

### How It Works

The pipeline has five components that run in sequence when you hit "Run Analysis" on the Strategy Builder tab.

**Component 1 — Tradeable Universe Provider (`advisors/universe_provider.py`):** Fetches the complete active US-equity tradeable set from Alpaca's paper trading host (roughly 12,700 symbols) and caches it for a week. This is a membership/validation set only — no ranking, no dollar-volume filter, no curated palette. Opus proposes tickers; the universe confirms they are real and tradeable; Composer's own `/backtest` endpoint is the final arbiter.

**Component 2 — Opus Build-Plan Generator (`advisors/build_plan_generator.py`):** Calls the Anthropic SDK with structured tool-use to generate 12 strategy build-plans per objective. Plans are expressed in an intermediate DSL (a typed pre-image of the `symphony_schema` constructor API) rather than raw Composer JSON. Opus proposes tickers from its own market knowledge; off-universe tickers are pruned; degenerate plans (fewer than one asset after pruning) are dropped; structurally-identical plans are deduplicated. The generation system prompt encodes the full Composer condition grammar, three compiler-verified DSL examples, and objective-specific signatures (e.g. `volatility_mitigation` requires an inverse-vol weight or a low-vol filter anywhere in the plan). A bounded retry fires if the model hits its output-token limit.

**Component 2b — Atlas Community Admission (inside `build_plan_generator.py`):** `load_atlas_candidates(objective)` pulls community strategies from **algo-db.com** (via its `captplanet.strategies` MongoDB Atlas collection, weekly-cached, bill-protected) and ranks candidates by the stat most relevant to the objective — lowest drawdown for `cut_drawdown`, lowest vol for `volatility_mitigation`, best Sharpe for `lift_risk_adjusted`, lowest pairwise Jaccard overlap for `diversify`. Up to 20 are admitted per run, tagged `provenance="atlas-suggested"`, and pooled with the built-new plans before the FDR gate.

**Component 3 — Plan-to-Tree Compiler (`advisors/plan_tree_compiler.py`):** Deterministically compiles each DSL plan into a Composer `raw_value` tree using only `symphony_schema` constructors. The full grammar is reachable: nested groups, equal/specified/inverse-vol weighting, filters, simple conditions, and compound conditions (`make_binary_compound_condition`, `make_compound_condition`). Every compiled tree is gated by `symphony_schema.validate_tree` before it is allowed to proceed. A bounded repair loop handles tradeability rejections (HTTP 400 from Composer — prunes the named ticker and retries) separately from grammar rejections (HTTP 422 — not blindly repaired, dropped instead). Plans using market-cap weighting are dropped immediately: Composer deprecated that node type and returns HTTP 422 for it.

**Component 4 — Weekly Scheduler (`advisors/strategy_builder_scheduler.py`):** A standalone script that runs the builder automatically once per ISO week for all four objectives. It includes a same-week idempotency guard (patchable by tests) and bounded per-objective retry. A failure on one objective is logged and the scheduler continues to the next. Invoke manually with `python -m advisors.strategy_builder_scheduler`. The on-demand route (`POST /ai-advisor/strategy-builder/run`) runs the same pipeline immediately.

**Component 5 — Downstream pipeline (unchanged except cull strengthening):** Backtests via Composer (1 request/second rate limit), then runs the full batch — both built-new and atlas-suggested candidates together — through the Harvey-Liu BHY FDR gate. The cull has been strengthened to autotuner grade:

- **PBO veto (new):** Probability of Backtest Overfitting is computed for each candidate via `math_engine.compute_pbo`. A candidate with PBO > 0.5 is vetoed regardless of FDR result.
- **SPY OOS benchmark (new):** The previously-always-zero OOS alpha baseline is replaced with a real SPY benchmark computed by backtesting a 100%-SPY tree over the same fold. Candidates that do not beat SPY OOS alpha are rejected. If SPY data is unavailable the gate fails conservatively (never silently passes everyone).
- **Rejection tagging:** Every rejected candidate carries a `rejection_reason` field — `pbo_veto`, `below_spy_alpha`, or `fdr_not_winner` — so you can see exactly why a candidate did not survive.

---

### Route and Error Boundary

The on-demand route (`POST /ai-advisor/strategy-builder/run`) now calls `build_plan_generator.load_atlas_candidates(objective)` for the objective-matched Atlas injection path. The previous unranked community adapter (`community_candidate_infos`) was deleted — it had no callers after this rewire.

Engine errors are no longer echoed verbatim in the JSON response (which could expose API key material in an exception message). The error branch now returns a fixed `"strategy-builder-error"` token and logs the real error server-side.

---

### Known Scope Boundaries

- **Advisory-only throughout.** The builder adds no new settings-write path, does not interact with `LIVE_EXECUTION`, and does not deploy strategies to Composer.
- **Composer `/backtest` is the final tradeability arbiter.** A ticker that passes the Alpaca membership check may still fail the Composer backtest — those candidates are dropped by the repair loop or the gate.
- **Market-cap weighting is not supported.** Composer deprecated the market-cap node type and returns HTTP 422 for it. The compiler detects market-cap plans and drops them before attempting a backtest.
- **The weekly scheduler does not gate on market hours.** It persists survivors as advisory observations keyed to `symphony_id=""` (unattached to any live symphony). Those proposals accumulate in the Strategy Builder tab for operator review.

---

## Test Hygiene — importlib.reload Removal (PR #64, 2026-06-21)

Internal test-infrastructure change, no user-visible behavior affected.

Thirty-seven `importlib.reload(...)` calls were removed from three test files in `tests/advisors/` (`test_community_strats.py`, `test_community_strats_timeout.py`, `test_atlas_cache.py`). These reloads installed a new module object on every test while other already-imported modules kept references to the old one, leaking a complete module copy per test. Under the project's default parallel-test mode (pytest-xdist, `-n auto`) the accumulation is sharded across workers and bounded; under single-process mode (`-n0`) the leak drove resident memory from roughly 8.1 GB to 6.9 GB in the `tests/advisors/` suite alone.

The reloads were dead weight: all three modules resolve their database path and patch targets from `os.environ` at call time, so the existing `monkeypatch.setenv` fixtures provide full isolation without re-executing the module. Patches were re-pointed to the correct module-attribute targets. Behavior is preserved: every test's assertions are unchanged. Three AST-based anti-recurrence guards (`test_no_importlib_reload_in_this_test_module`) were added, one per file, to prevent the pattern from returning.

The residual ~6.9 GB single-process footprint is dominated by cumulative heavy-library retention (quantstats, Optuna, anthropic SDK) across the full `tests/advisors/` suite. That is a separate concern: it is bounded per-worker under xdist, it is not a production daemon leak, and it is tracked as a low-priority follow-on item in `feature-plans/BACKLOG.md`.

---

## Atlas Community-Strategies Cache Fix (PR #66, 2026-06-21)

The community-strategies weekly cache — protecting algo-db.com's `captplanet.strategies` Atlas collection — was silently broken: every attempt to populate it failed, so every Strategy Builder run fetched live from algo-db.com's Atlas even when a cached result should have been served.

Two bugs were responsible. **Bug 1 — ObjectId serialization:** The MongoDB projection used to fetch community strategies did not suppress the `_id` field. MongoDB's default `_id` is a BSON `ObjectId`, which is not JSON-serializable. The `atlas_cache` layer called `json.dumps` on the result to write it to the cache; the `TypeError` was swallowed, and the cache row was never written. Every subsequent call re-fetched live from Atlas. The fix adds `"_id": 0` to the projection. **Bug 2 — unbounded fetch OOM:** The original fetch retrieved all matching documents with no server-side limit. The Atlas collection holds roughly 11,000 strategy documents; pulling them all in one call exhausted the 4 GB droplet's memory and killed the process. The fix applies a server-side sort (`oos_metrics.sharpe` descending) and limit (`_MAX_FETCH_DOCS=500`) inside the fetch function so only the top candidates are transferred. The `atlas_cache` upsert was also hardened with `json.dumps(..., default=str)` as a defense-in-depth serialization guard.

After these fixes the cache populates correctly on first access and serves the cached result for the remainder of the week. The Strategy Builder admitted 481 community candidates on the first live run post-fix. Advisory-only; no execution-path impact.

---

## Test and CI Housekeeping (PR #67, PR #68, 2026-06-21)

Internal changes, no user-visible behavior affected.

**PR #67 — news-corpus recency test determinism:** A recency-scoring test was anchored to `datetime.now()` rather than a fixture timestamp. As wall-clock time advanced, the exponential-decay weight of a fixture article drifted, eventually causing the test to fail. The fix anchors the "now" reference used by the scoring function to the fixture's own timestamp, making the assertion deterministic regardless of when the test runs.

**PR #68 — GitHub Actions CI restored to green:** The automated test pipeline (`ruff format --check` + full pytest) had been continuously failing since the initial project setup due to formatting debt in the Strategy Builder modules introduced during the #63 development cycle. Thirty-four files were reformatted to satisfy `ruff`'s style rules, and ten pre-existing tests that relied on wall-clock time or uncontrolled external state were made hermetic. The CI workflow now runs and passes cleanly on every push. Automated test gating is active.
