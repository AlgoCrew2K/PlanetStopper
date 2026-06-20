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
