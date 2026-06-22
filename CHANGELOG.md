# Changelog

---

## Self-Healing Symphony Dashboard

When the daemon starts and finds no symphonies recorded — for example after a fresh deployment or a database reset — it now seeds your live Composer symphonies into the dashboard immediately, without waiting for the next market-hours data cycle.

Previously, a database reset or first-time deployment on a weekend would leave the dashboard blank until the market reopened Monday morning. The seeding now runs at startup regardless of market hours, so your dashboard reflects your live Composer portfolio within seconds of the daemon coming online.

The seeding is fully idempotent: if your symphonies are already recorded, nothing changes. It does not write synthetic or placeholder performance history — only the symphony entries themselves are created, keeping your real performance tracking clean.

---

## Symphony Builder + Community Suggester

The AI Advisor's **Strategy Builder** tab now does two things together every time you run an analysis.

### Symphony Builder

Builds **brand-new strategy symphonies from scratch**, shaped to the objective you choose:

- **Diversify** — reduce correlation across your existing portfolio
- **Cut drawdown** — lower peak-to-trough losses
- **Lift risk-adjusted return** — improve Sharpe-family metrics
- **Volatility mitigation** — dampen intraday swing exposure with inverse-vol or low-vol filters

Claude Opus designs strategies from scratch, proposes its own tickers based on market knowledge, validates each one against the live tradeable US-equity universe (~12,700 symbols via Alpaca), compiles them into valid Composer trees, and runs every candidate through the same BHY/FDR statistical anti-overfit gate that the autotuner uses. Only candidates that clear the gate are surfaced to you. Zero survivors is a valid, non-error outcome — the gate is the point. You apply any survivor in Composer yourself; the Builder never deploys anything on your behalf.

The Builder also runs automatically once per week across all four objectives, accumulating proposals on the Strategy Builder tab for your review.

### Community Symphony Suggester

Alongside its own built-new symphonies, the Builder also **sources and surfaces existing community symphonies** from [algo-db.com](https://algo-db.com) — an independent community database of operator-published Composer strategies. The Suggester:

- Fetches community symphonies from algo-db.com and filters by minimum out-of-sample Sharpe
- Validates each candidate tree against the Composer grammar before it enters the pipeline
- Runs them through the same Composer backtest + FDR gate as the built-new candidates, so both sets are held to the same statistical bar
- Tags each surviving candidate with its provenance (`built-new` vs `atlas-suggested`) so you can always tell where a proposal came from

Community data is cached weekly to keep read costs bounded; the Suggester degrades gracefully to built-new-only when the community source is unavailable. Up to 20 community candidates are admitted per run alongside the built-new designs.

Both built-new and community candidates appear together in a single ranked result on the Strategy Builder tab.

---

## AI Council — Nightly Market Prism

Each night at 03:00 America/New_York, a council of six Claude agents produces a structured overnight market read stored as a **Market Prism** observation and rendered on the AI Advisor Overview tab.

### What you see each morning

- **Sentiment chip** — a top-line directional verdict: risk-on, risk-off, or neutral
- **Rationale paragraph** — the synthesizer's plain-language justification
- **Per-lens digest** — independent reads from five analytical domains:
  - **Technicals** — moving-average posture, market breadth, and 20-day price momentum from 270 days of Alpaca daily bar history
  - **Sentiment / News** — GDELT tone scalar plus a ranked, deduplicated corpus from GDELT artlist and eight RSS/Atom feeds (CNBC, MarketWatch, Yahoo Finance, Federal Reserve, BLS, BEA, SEC 8-K, Google News); articles scored by recency, market relevance, and source authority
  - **Derivatives** — FRED VIXCLS (spot VIX) and VXVCLS (3-month VIX); volatility regime and term-structure read
  - **Macro** — FRED series: 10-year Treasury yield, unemployment rate, CPI, Fed Funds effective rate; requires `FRED_API_KEY`
  - **Fundamentals** — SEC EDGAR companyfacts for the portfolio universe and a proxy basket of large-cap equities; no API key required
- **Cited sources** — links to the underlying data used by the council session

When no council run exists yet, the Overview shows an informative empty state rather than a blank section. Each lens follows an honest-availability contract: when its data source is unreachable, it reports unavailable rather than fabricating values.

### How it works

Five lens analysts independently read their data sources and file initial reads. The synthesizer waits until all five are confirmed in the audit database, then conducts structured Q&A and optional debate (capped at three rounds) before writing a single integrated verdict. Every session phase is written to an audit log keyed by run ID. The scheduler retries failed runs up to three times and only counts a run as successful when the `MARKET_PRISM` observation row is confirmed in the database — an exit-zero-without-row is retried.

**Advisory-only.** The council verdict does not affect Guard Alpha trailing-stop logic, autotuner parameters, or any trade or exit decision.

### Billing

The nightly council runs against the operator's Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`), not the metered API key — council runs do not accrue per-token costs against `ANTHROPIC_API_KEY`. The on-demand AI Advisor routes continue to use the API key.

---

## Guard Alpha — Risk Engine and Performance Dashboards

### Guard Alpha risk engine

Guard Alpha watches each Composer symphony minute-by-minute and exits to cash when the math says the day's gain is at risk. Each minute tick evaluates four independent exit signals:

- **VWAP Breakdown** — a sharp move through the volume-weighted average price
- **Take-Profit** — an exceptional upside the regime is unlikely to sustain
- **VWAP Bleed Cut** — a slow erosion that a sharp-cross detector would miss
- **Trailing Stop** — the broad catch-all, tightening through the day on a theory-derived time-squeeze curve

A Monte-Carlo gate bootstraps thousands of paths over historically similar days to estimate recovery probability before firing a trailing-stop exit. When today's market conditions are unprecedented, the MC gate is suppressed so the protective stop can fire on price action alone.

Every constant in the risk math is named and carries a provenance comment. The autotuner re-fits each symphony's parameters weekly using risk-aversion-shaped utility (CRRA-EU) and a Benjamini-Hochberg-Yekutieli overfitting haircut — if the winner cannot be statistically distinguished from luck, no parameters deploy.

### History and Performance dashboards

- **History** — past exit decisions and daily outcomes per symphony
- **Performance** — returns, Sharpe/Sortino, drawdown, win-rate, and the live-vs-counterfactual Guard Alpha comparison (realized exit return vs. holding to the close). Shows an "insufficient history" banner below minimum sample size so underpowered metrics are not displayed as precise.
- **Guard Alpha $-saved panel** — headline total of dollars saved across all Guard Alpha exits since go-live, with event count, rendered below the dashboard hero

---

## AI Advisor

The AI Advisor (`/ai-advisor`) is an **advise-only** surface. Nothing on it acts on your behalf; every proposal is a hypothesis for you to review, accept, or reject.

### What the six tabs do

- **Overview** — the most recent Market Prism council verdict; observation history; per-symphony assessment
- **Correlations** — pairwise return-correlation across your current symphonies; estimates carry mandatory crisis caveats (correlations converge toward 1.0 in stress — when de-correlation matters most, the estimate is least reliable)
- **Asset Swaps** — objective-directed swap proposals; every candidate is backtested on Composer and screened through the FDR gate before surfacing; zero survivors is a valid outcome
- **Logic Changes** — parameter-tweak proposals; same backtest-and-gate pipeline; the full candidate batch goes through the gate as one set so the multiple-testing correction applies correctly
- **Chat** — contextual explanation of any surfaced artifact (a verdict, a correlation, a proposal); read-only, no proposal or write path
- **Strategy Builder** — Symphony Builder + Community Suggester (see above)

### Config advisor

On demand, the config advisor assembles a credential-free context for a symphony and asks Claude for structured, risk-classified parameter suggestions. Only an explicit allowlist of tunable parameters can enter the context — no credential, account ID, safety flag, or methodology constant can reach the model. Accepting a suggestion applies it for the next engine cycle; rejecting it leaves parameters unchanged.

---

## Dashboard and Deployment

### Dashboard password auth gate

When deployed publicly, the entire dashboard (all tabs, all `/api/*` routes) is protected by a single-password login gate. Unauthenticated browser requests redirect to `/login`; unauthenticated API and XHR requests return a 401. The gate is **fail-closed**: a missing `DASHBOARD_PASSWORD` or `SECRET_KEY` causes the server to refuse all requests rather than serve open. Supports both plaintext and hashed (`pbkdf2:`/`scrypt:`/`bcrypt:`) password storage.

### Production deployment

The production deployment runs on a DigitalOcean VPS (NYC3). Caddy handles TLS termination with a Let's Encrypt certificate; the Flask daemon binds to `localhost:8090` only, behind a firewall restricting external access to ports 22, 80, and 443. Full runbook in `docs/DEPLOYMENT.md`.

`LIVE_EXECUTION='False'` is set permanently on the droplet. The droplet operates in shadow/advisory mode — no live trading.

---

## Known Scope Boundaries

- **Advisory-only throughout.** The council, AI Advisor, and all proposal engines are entirely off the execution path. None of them read or write `LIVE_EXECUTION`, interact with trade orders, or affect Guard Alpha exit decisions.
- **`LIVE_EXECUTION` is permanently `False` on the production droplet.** No live trading is performed there.
- **The council's regime verdict is not wired to the risk engine.** Risk-on / risk-off is surfaced for operator information only.
- **The AI Advisor never deploys anything on its own.** Every accepted change is applied by the operator in Composer.
- **GDELT reachability is environment-dependent.** GDELT operates correctly on the production Linux VPS; it may be intermittently unreachable from development environments. An unreachable GDELT degrades the sentiment lens gracefully rather than failing the council.
