# Planet Stopper

> An institutional-grade algorithmic **risk engine** for retail Composer.trade operators. Composer holds a basket of rule-based ETF rotation strategies ("symphonies") through the trading day; Planet Stopper watches each one minute-by-minute and liquidates it to cash when the math says the day's gain is at risk. It does not pick what to hold or when to enter — it provides exit discipline.

Planet Stopper is a single Python/Flask daemon. It runs on a machine you control during US market hours, pulls 1-minute prices from Alpaca, evaluates four independent exit signals against a Monte-Carlo "is today actually bad?" sanity check, and — when configured to act — fires a liquidation through Composer's API. It reports every decision to Discord and surfaces everything on a read-only dashboard. An overnight Optuna autotuner re-fits each symphony's parameters using risk-aversion-shaped utility and an overfitting haircut, so the parameters the engine runs on are not cherry-picked from a backtest. On top of all this sits an **advise-only AI Advisor** that diagnoses your portfolio, proposes asset swaps and logic tweaks, backtests every proposal on Composer, runs each one through the same overfitting acceptance gate the autotuner uses, and surfaces only the survivors — with honest caveats — for you to decide on. It never deploys anything on its own.

---

## Table of contents

1. [Is this for you?](#1-is-this-for-you)
2. [Core concepts](#2-core-concepts)
3. [Architecture](#3-architecture)
4. [How one cycle works (the `:00` tick)](#4-how-one-cycle-works-the-00-tick)
5. [The dashboard](#5-the-dashboard)
6. [The AI Advisor](#6-the-ai-advisor)
7. [The risk math, in plain language](#7-the-risk-math-in-plain-language)
8. [Optimization: the Optuna autotuner](#8-optimization-the-optuna-autotuner)
9. [Integrations](#9-integrations)
10. [Data model](#10-data-model)
11. [Running it](#11-running-it)
12. [Testing](#12-testing)
13. [Safety boundaries](#13-safety-boundaries)
14. [What Planet Stopper does NOT do](#14-what-planet-stopper-does-not-do)

---

## 1. Is this for you?

Planet Stopper is an **exit-discipline overlay**, not a hands-off product and not a trading strategy of its own. Read this section before running anything.

### A good fit if

- You operate one or more **Composer.trade symphonies** (Composer's name for a rule-based ETF rotation strategy) and hold them through the day.
- You accept that Composer does not actively manage intraday drawdowns, and you want a tool that watches each symphony every minute and exits to cash when the math says the day's gain is eroding.
- You are comfortable running a Python daemon on a machine that is on during US market hours (a home server, a small VPS, a workstation).
- You can hold an **Alpaca** data subscription so the daemon can pull 1-minute prices for the underlying ETFs.
- You are willing to read a dashboard and a daily Discord post-mortem, and to intervene when needed.

### A poor fit if

- You want a bot that **enters** positions, picks symbols, or sizes trades. Entry and sizing are entirely Composer's job — Planet Stopper only decides when to *exit*.
- You want a high-frequency intraday trader. The cadence is one decision per minute per symphony — appropriate for equity-ETF strategies, not for futures or crypto.
- You want a single "buy/sell, confidence X" number from a forecasting model. Planet Stopper deliberately surfaces several independent signals and reconciles them by a fixed priority order; there is no master forecast.
- You want the AI Advisor to change your strategy for you. It only *proposes* — every change is applied by you, by hand, in Composer.
- You are not prepared to watch a dashboard or investigate alerts.

### How to evaluate fit honestly

Run the daemon with `LIVE_EXECUTION=False` (paper mode) for at least two weeks against your live symphonies. The engine evaluates every cycle and posts Discord alerts exactly as if it were live, but does **not** call Composer's liquidation endpoint. Compare its "would-have-exited" decisions against Composer's actual end-of-day outcomes. If the decisions feel right and add value over buy-and-hold, flip the switch.

---

## 2. Core concepts

A few terms recur throughout this document:

| Term | Meaning |
|------|---------|
| **Symphony** | Composer's unit of strategy: a rule-based ETF rotation you authored or licensed. Planet Stopper manages risk **per symphony** — never as a portfolio aggregate. |
| **Guard Alpha** | The P&L difference between exiting early (when Planet Stopper fires) and holding to the close. The product's reason for being. |
| **Trailing stop** | A stop-loss level that ratchets *up* as the symphony's return rises but never moves down. When the return falls back to it, the engine exits. |
| **Monte Carlo (MC) gate** | A bootstrap simulation over regime-similar history that answers "from here, how often did this regime recover by the close?" — used to *veto* a noisy exit, never to force one. |
| **CVaR** | Conditional Value-at-Risk: the average loss in the worst slice of outcomes. A tail-risk diagnostic shown to the operator; never a live trigger. |
| **CRRA-EU utility** | Constant-Relative-Risk-Aversion expected utility — a textbook formula for an investor who dislikes losses more than equal-sized gains. The autotuner optimizes for it. |
| **Walk-forward** | Splitting history into train / validation / held-out folds so parameters are scored on data they were not fitted on. |
| **Acceptance gate** | The overfitting screen that raises the bar a candidate must clear in proportion to how many candidates were tried (a multiple-testing / FDR correction). The autotuner uses it to certify parameters; the AI Advisor reuses it to screen every proposal. |
| **NN1 spec-freeze** | A discipline that records *why* every constant has its value and refuses to deploy parameters chosen because "the backtest liked them." |

### The risk thesis

Composer symphonies are fully invested through the day and rebalance on Composer's own schedule. On a bad day, a symphony can give back a meaningful gain — or turn a gain into a loss — before Composer's logic reacts. Planet Stopper's thesis is that a **disciplined, math-gated trailing stop** can capture that giveback as realized cash, while a Monte-Carlo sanity check prevents the bot from capitulating at a noisy local low that the regime usually recovers from.

The engine deliberately uses **multiple independent exit signals** rather than one combined "master signal." Different signals catch different failures: a sharp liquidity break, an exceptional upside that the regime won't sustain, a slow VWAP bleed, and a generic trailing-stop hit. Reporting every signal that co-fired (not just the winner) lets the operator distinguish a high-conviction "everything fired at once" exit from a single-signal noise spike.

---

## 3. Architecture

Planet Stopper is a **monolithic Flask daemon** built around a one-minute scheduler. There is no message bus, no microservices, no external job queue — just a process, a scheduler, and two SQLite databases.

### Top-level modules

| Module | Role |
|--------|------|
| `app.py` | The Flask web app **and** the minute-by-minute scheduler. Renders the read-only dashboard, serves the JSON APIs and the `/ai-advisor` surfaces, enforces a single-daemon pidfile, and at every `:00` spawns the execution engine as a subprocess. |
| `alpha_bot_execution.py` | The **core engine**. One pass per `:00` tick: snapshot Composer holdings, fetch Alpaca prices, walk the risk-math layers per symphony, resolve a single exit decision, and (in live mode) fire liquidations. Also drives the end-of-day post-mortem and kicks off the weekly autotune. |
| `math_engine.py` | **Pure risk math**, no I/O: volatility scaling, intraday time-squeeze, parabolic ratchet, breakeven lock, VWAP signals, Monte-Carlo gate, regime-match guard, CRRA-EU utility, CVaR, and the exit-priority resolver. Every numeric constant is named and carries a provenance comment. |
| `autotuner.py` | The **Optuna walk-forward optimizer**: 125 trading days, 500 trials per symphony, CRRA-EU objective, BHY overfitting haircut, and NN1 spec-freeze enforcement. Invokes the three observer advisors after each run. |
| `acceptance_gate.py` | The reusable **overfitting acceptance gate** — the one-directional brake that decides whether a candidate clears the multiple-testing bar. Used by the autotuner to certify parameters and by the AI Advisor to screen every proposal. |
| `database.py` | The **state DB** schema, migrations, and accessors. Owns separate read/write and read-only connection helpers; the dashboard side only ever opens read-only. |
| `synthetic_history.py` | Fetches 125 days of 1-minute Alpaca history in parallel (with a file cache) and feeds the autotuner's day-by-day replay. |
| `reporting.py` | Discord webhooks and QuickChart embeds — exit alerts and the daily EOD post-mortem snapshot. |
| `analytics.py` | Performance analytics for the dashboard: returns, Sharpe/Sortino, drawdown, win-rate, and the live-vs-counterfactual comparison. |
| `ai_advisor.py` | The Claude-backed **config advisor**: assembles a curated, credential-free context for a symphony and asks an LLM for advise-only config-tuning suggestions. See §[6](#6-the-ai-advisor). |
| `symphony_logic.py` | Helpers for reading and summarizing a symphony's current state. |
| `advisors/` | The **AI Advisor** package: the proposal suite (de-correlation diagnostic, Composer backtest client + gate engine, asset-swap engine, logic-change engine, explain-only chat) plus three post-autotune observer producers. None of it ever trades. See §[6](#6-the-ai-advisor). |
| `engine/` | Small helpers used by the engine — exit-authority display badge and per-mode parameter resolution. |
| `dashboard/` | Row-building helpers for the dashboard tables. |

### The two-database pattern

Planet Stopper keeps **two SQLite databases** with a strict separation of duties:

- **State DB** — live positions, exit decisions, telemetry, advisor observations, spec bundles. Owned by the engine. The dashboard opens it **read-only**.
- **Optimization DB** — Optuna studies and trial history. Owned by the autotuner.

The two are **never cross-joined in application code**. If a row is needed in both, it is copied. This guarantees that a corrupt Optuna study cannot poison live state, and a stale dashboard read cannot affect tuning.

### Why subprocess-per-tick

At every `:00`, the Flask process forks a non-blocking thread that runs the engine in a **fresh subprocess** and tees its output to the daemon log. This is deliberate:

- **Crash isolation** — if the engine crashes, the dashboard keeps serving and the next minute's tick spawns clean.
- **A hard runtime ceiling** — a cycle cannot consume more than a minute without simply being followed by the next tick; nothing blocks the scheduler thread.
- **Auditability** — every tick is exactly one process and one set of database writes.

The architecture also obeys a hard rule: **no blocking I/O on the dashboard's request path.** Account totals and Composer stats are refreshed on the scheduler into an in-memory cache so dashboard requests never wait on a live API call.

---

## 4. How one cycle works (the `:00` tick)

This is the end-to-end walkthrough of what happens each minute during market hours.

**Step 0 — The scheduler fires.** The Flask process registers `schedule.every().minute.at(":00")` jobs: one spawns the engine, one refreshes account totals; a separate daily job prunes old trigger telemetry. At `:00`, a daemon thread launches `alpha_bot_execution.py` as a subprocess.

**Step 1 — The engine starts.** It reads the configured account UUIDs, opens a state-DB connection, and iterates per account, per symphony. For each symphony it snapshots Composer holdings, fetches today's 1-minute Alpaca bars, and computes the current return.

**Step 2 — Update the high-water mark.** The symphony's intraday high-water mark is raised to the max of its prior value and the current return. It never decreases within a day and resets at the close.

**Step 3 — Run the Monte-Carlo gate (deterministically).** The engine bootstraps thousands of paths over the regime-similar historical days to estimate the probability the symphony ends the day above where it is *now*. The seed is derived by hashing the cycle ID (`YYYYMMDD_HHMM`), so two daemon restarts at the same minute reproduce identical results. If the symphony lacks enough history, the gate returns a `None` sentinel and the protective stop proceeds on its own.

**Step 4 — Check the regime-match quality.** Before trusting the MC estimate, the engine measures how *close* today actually is to its nearest historical neighbors. If today is an **unprecedented** outlier (its neighbors are all "least-bad fits"), the MC veto is suppressed — the recovery probability is overridden to `None` — so the bot does not lean on a Monte-Carlo estimate built from unrepresentative days. This is the fail-safe described in §[7.4](#74-monte-carlo-gating-and-the-regime-match-guard).

**Step 5 — Walk the math layers.** In order: 20-day volatility scaling sets the base stop width; the intraday time-squeeze tightens it through the day; the parabolic ratchet tightens further on fast moves; the breakeven lock prevents the stop from dropping below entry once latched; the two VWAP layers arm a breakdown signal and a slow-bleed signal; and the exit-confirmation gate requires several consecutive ticks below the stop line plus the MC sanity check.

**Step 6 — Compute the four exit-trigger flags.** Out of those layers fall exactly four boolean triggers: **VWAP Breakdown**, **Take-Profit**, **VWAP Bleed Cut**, and **Trailing Stop**.

**Step 7 — Resolve priority.** If any flag fired, `resolve_trigger_priority` selects the single canonical winner by a fixed order — `VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop` — and returns the winner together with every co-fired flag as telemetry.

**Step 8 — Act and report.** If a trigger won and `LIVE_EXECUTION=True`, the engine queues the symphony and drains the queue at the end of the pass, calling Composer's liquidation endpoint with exponential backoff for resilience against rate limits. It then posts a Discord alert (exit reason, Guard Alpha vs. hold-to-close, VWAP stats, and a QuickChart summary). If no trigger fired, the engine records a CVaR diagnostic and advances the symphony's state by one tick.

**End of day.** In the post-close window the engine produces a two-stage post-mortem: it first locks the day's true shadow returns and Guard Alpha from live Alpaca prices, then injects tomorrow's target holdings after Composer's rebalance without overwriting the locked math. On Fridays/weekends it then runs the weekly autotune (§[8](#8-optimization-the-optuna-autotuner)).

---

## 5. The dashboard

The dashboard is a Flask web UI on `http://localhost:5000` (overridable via the `PORT` env var). **It is an observability surface, never an action surface for live trades.** It has no button that places, cancels, or modifies a normal trade, and it cannot spawn the engine — the scheduler is the only legal engine spawner.

This read-only stance is enforced in depth:

1. **Architecture rule.** The project's hard constraint: the dashboard is read-only for live trades.
2. **Driver-level.** Every dashboard database accessor opens SQLite in read-only mode. A Flask request thread literally cannot run a write transaction against the state DB.
3. **Code-level.** The manual `/api/trigger` endpoint is intentionally disabled and returns an explicit "manual trigger disabled — use the scheduler" message.

The dashboard's tabs:

- **Home (`/`)** — per-symphony live state: current return, distance to the active trailing stop, status (idle / armed / exiting), Monte-Carlo probability with a regime-match indicator, VWAP and VWAP-bleed thresholds, the CVaR diagnostic, and a feed of recent decision events.
- **History (`/history`)** — past exit decisions and daily outcomes.
- **Performance (`/performance`)** — returns, Sharpe/Sortino, drawdown, calmar, win-rate, and the live-vs-counterfactual ("Guard Alpha") comparison. The route surfaces an "insufficient history" banner below a minimum sample size so underpowered metrics are not shown as precise.
- **AI Advisor (`/ai-advisor` and its sub-tabs)** — the config-advisor surface, the autotune/advisor-observation feed, and the proposal suite: correlations, asset swaps, logic changes, and explain-only chat (§[6](#6-the-ai-advisor)).
- **Settings (`/settings`)** — the **one** normal write path in the dashboard: editing operator-config rows (algorithm parameters and webhook URLs). It never touches positions or trades, and secrets are masked.

The only operator-initiated *trade* surface is a deliberate **panic button** — a manual "sell account to cash" endpoint (`/api/sell_account`) the operator must explicitly click. The engine never fires it autonomously, and the AI Advisor has no path to it.

---

## 6. The AI Advisor

Planet Stopper's AI surface is **advise-only, end to end**. Nothing on it acts on your behalf; everything it produces is a hypothesis, a proposal, or an observation for a human to read, accept, or reject. It has three distinct parts: the **proposal suite** (the headline feature), the **config advisor**, and the **observer producers**. All three live in or alongside the `advisors/` package and surface on the `/ai-advisor` tabs.

### 6.1 The proposal suite (the headline feature)

The proposal suite turns "should I change this symphony?" into a disciplined, backtested, overfitting-screened recommendation — without ever touching your live positions. The whole suite runs **offline**, never on the 1-minute execution path, and only ever calls Composer's *read* and *stateless-backtest* endpoints — never a write, mutate, or trade-placement call.

The loop is the same for every proposal:

> **diagnose → propose → backtest on Composer → run through the overfitting acceptance gate → surface gated survivors-with-caveats → operator decides → operator applies the change by hand in Composer.**

The pieces, in order of the loop:

- **De-correlation diagnostic** (`correlation_diagnostic.py`) — pure measurement. It computes pairwise Pearson return-correlation across your current symphonies from their historical return series. No API call, no gate, no DB write. Every estimate carries a mandatory **crisis caveat**: correlations destabilize toward 1.0 in market stress — exactly when de-correlation matters most, the estimate is least reliable. Thin windows are flagged as `thin_data` rather than presented as precise. This diagnostic is what *motivates* a de-correlation objective for the swap engine.

- **Composer backtest client** (`composer_backtest_client.py`) — submits an inline symphony definition to Composer's `POST /api/v0.1/backtest` and returns a typed result (per-day returns + stats). It **never raises**: any API or transport failure returns a result with `stats=None` and an error string, so one candidate's failure cannot abort a batch. It retries transient errors with exponential backoff and respects Composer's rate limit and `Retry-After` headers.

- **Backtest gate engine** (`backtest_gate_engine.py`) — the reusable spine. For a batch of candidates it (1) applies the **fold-transform**, slicing each candidate's Composer backtest series into the *same* walk-forward fold structure (train/validation, with purge and embargo) that the autotuner uses, then (2) runs the **BHY/Yekutieli FDR correction across the full candidate set** (N candidates = N trials in the multiple-testing sense), and (3) calls `acceptance_gate.evaluate_acceptance_gate(...)` **unchanged** for each candidate. The result is a gated batch: every candidate annotated with its verdict and honest caveats. Two load-bearing invariants hold: the gate is a **one-directional brake** — no discretionary score can resurrect a veto-failed candidate — and a series too thin to produce a purge-respecting validation fold yields a **WITHHOLD**, never a fabricated pass.

- **Asset-swap engine** (`asset_swap_engine.py`) — proposes swapping one asset for another over the Composer ETF universe. Every swap is **objective-directed**: the operator (or the diagnostic) states a measurable objective — reduce correlation, reduce drawdown, or lift risk-adjusted return — and the engine searches *toward* that objective rather than brute-forcing combinations. Operator-initiated mode tries one named swap; advisor-suggested mode shortlists objective-driven candidates. Each candidate is backtested via the backtest client and screened by the gate engine as a single batch (so the FDR correction sees all N). Survivors are persisted as advise-only observations and surfaced with an "apply this manually in Composer" instruction. Zero survivors is a valid, non-error outcome.

- **Logic-change engine** (`logic_change_engine.py`) — proposes parameter tweaks to a symphony's decision logic (the highest overfitting-risk capability). Same objective-directed discipline, same backtest-then-gate flow. Critically, it feeds the **entire batch of N candidates as one call** to the gate engine so the multiple-testing correction applies across all of them jointly — gating candidates individually would silently disable the FDR denominator and is forbidden. The candidate count is bounded per run, because an unbounded search makes the FDR correction ineffective in practice.

- **Explain-only chat** (`advisor_chat.py`) — a contextual "chat about this" backend. You point it at a *specific* surfaced artifact (a gate verdict, a correlation result, a swap or logic-change proposal, an observation) and it explains that artifact in plain language. It is a **hard boundary**: chat cannot issue trade directives, cannot propose/apply/accept any change, cannot generate new unvalidated recommendations, and has no write path. The boundary is enforced both by the system prompt and structurally — the module imports no write, trade, or config-mutation surface. Like the rest of the AI surface it never raises; with no LLM key it returns a clear "chat unavailable" message.

The suite is surfaced across the AI Advisor sub-tabs: **Correlations** (`/ai-advisor/correlations`), **Asset Swaps** (`/ai-advisor/asset-swaps`), **Logic Changes** (`/ai-advisor/logic-changes`), and **Chat** (`/ai-advisor/chat`). Each is a read-only surface; the "evaluate" endpoints run the offline backtest-and-gate pipeline and render the gated results.

### 6.2 The config advisor (`ai_advisor.py`)

On demand from the dashboard, the config advisor assembles a curated, **credential-free** context for a symphony — its current values for a small allowlist of tunable parameters, the data-window limits, and the engine's hard risk invariants — and asks an LLM (via the Anthropic SDK) for structured, risk-classified suggestions.

Key properties:

- **Allowlist, not denylist.** Only an explicit set of suggestible parameters can ever enter the context. No credential, account ID, safety flag, or methodology knob (such as the risk-aversion γ) can reach the model. Locked variables that Optuna never tunes are excluded from suggestion.
- **Risk polarity is supplied.** Each suggestible parameter carries a one-line definition and whether raising it loosens or tightens risk, so a suggestion that would functionally loosen a live stop is self-flagged as risk-increasing.
- **Hypotheses, not validations.** The role framing tells the model it is an operator-assist analyst whose suggestions are *unvalidated hypotheses* for a human and the walk-forward validator to test. An empty suggestion list is an explicitly encouraged answer.
- **Never raises.** A model-call failure is "no suggestion this click" with zero engine impact — it degrades to an error message, never an exception on a live path.

The operator reviews each suggestion on the `/ai-advisor` tab and explicitly **accepts** or **rejects** it via the `/ai-advisor/accept` and `/ai-advisor/reject` endpoints. Accepting a suggestion records the operator's decision; it does not auto-apply to live trading.

> **Note:** The config advisor and the chat backend require the Anthropic SDK and an API key to produce output. Neither is required to run the daemon; without a key, the relevant tab simply reports that no suggestion / no chat is available.

### 6.3 The observer producers (`advisors/`)

After each autotune run, three **independent** observer producers write observations to the database. They share no synthesized verdict — each is independently testable and independently failure-resilient, so if one crashes the others still run. The operator reads them on the `/ai-advisor` tab.

All three read the database through a dedicated read-only query helper, and the held-out frozen-eval fold is structurally invisible to them — this protects the integrity of the walk-forward held-out set. A test enforces that no advisor module opens a direct write connection.

- **Overfitting Conscience** (`overfitting_conscience.py`) — watches the researcher-degrees-of-freedom counter against the autotuner's effective-test budget. It flags any backtest-selected facet, escalates when researcher degrees of freedom exceed a fraction of the trial budget, and watches for that counter growing run-over-run. A clean reading is the signal that the autotuner is operating in its honest steady state.
- **Spec Critic** (`spec_critic.py`) — checks the spec-bundle tables for structural integrity: that the required THEORY-frozen facets (the risk-aversion γ, the utility family, and the wealth argument) are present and frozen, that every facet's freeze discipline is recognized (default-deny on anything unknown), and that no out-of-scope facet has been seeded prematurely.
- **Divergence Explainer** (`divergence_explainer.py`) — surfaces the state of a second, operator-configurable CVaR window when that feature is enabled. By default the feature is **off**, and the advisor writes a "not applicable" observation each cycle to keep the audit trail complete. It is structurally forbidden from ever persisting or displaying a signed divergence quantity — see §[7.6](#76-cvar-a-diagnostic-not-a-trigger).

---

## 7. The risk math, in plain language

Each subsection gives the intuition first, then the mechanics. Every constant in `math_engine.py` is named and carries a source comment; several have published references, and a few are practitioner heuristics that the project flags honestly.

### 7.1 Volatility-scaled trailing stop

The trailing stop's width scales with the symphony's recent volatility — tight in a quiet symphony, wide in a noisy one, so background noise does not trip an exit. The volatility estimate uses a **20-day rolling window** (the institutional standard, anchored by RiskMetrics). The stop ratchets up with the high-water mark and never moves down.

### 7.2 Intraday time-squeeze

The stop *tightens* through the trading day on a concave, front-loaded curve — wider at the open, tighter at the close. The curve is `f(t) = 1 − √(1 − t)`, where `t` is the fraction of the session elapsed. This is closed-form **THEORY with zero free parameters**: under square-root-of-time scaling for i.i.d. returns, the standard deviation of the *remaining* session scales as `√(1 − t)`, so the natural tightening is `1 − √(1 − t)`. (Reference: Danielsson & Zigrand, 2003.)

### 7.3 Parabolic ratchet

When a symphony's return moves up fast (a "parabolic squeeze"), the stop tightens further to lock in the move. Arming is decided by velocity — the change in return between consecutive ticks — against a threshold; once armed it does not re-arm within the day. At each day boundary the prior-return reference resets so velocity reads as zero on the first tick, preventing a false arm on an opening gap. The velocity threshold and squeeze cap are practitioner-grade values the autotuner is allowed to search.

### 7.4 Monte-Carlo gating and the regime-match guard

Before the engine fires a *trailing-stop* exit it asks: in the historically most similar days to today, how often did the symphony end the day above where it is now? It answers by bootstrapping thousands of paths over the **k-nearest-neighbor regime-matched** days. If the recovery probability sits in the arming band, the stop arms; if it is high enough, the stop disarms.

Two safeguards keep this honest:

- **It is a delay, not a forecast.** A high recovery probability *vetoes* an exit; it never *forces* one. If price keeps falling, the next tick re-checks.
- **The regime-match guard.** The MC estimate is only as good as the match between today and its neighbors. The engine measures that match distance; when today is an **unprecedented** outlier, it suppresses the MC veto entirely so the protective stop can fire on ticks-below-stop alone. This closes the classic failure mode where the gate is least informative exactly when the regime is breaking.

The empirical bootstrap, kNN regime-matching, and Monte-Carlo simulation are each individually well-established; their **combination as an exit veto is unconventional**, and the regime-match guard exists precisely to bound the soft spot.

### 7.5 VWAP signals and breakeven

Two VWAP-based signals run alongside the trailing stop:

- **VWAP Breakdown** — a fast, hard-cut signal for a sharp move through the volume-weighted average price.
- **VWAP Bleed Cut** — a slower signal for a gradual erosion that a sharp-cross detector would miss, armed off a volatility-scaled threshold.

Both are suppressed during a short post-open grace window so opening volatility does not trip a false exit. The **breakeven lock** is a one-way latch: once enough qualifying ticks have accumulated near the activation level, the stop is pinned never to drop below entry.

### 7.6 CVaR: a diagnostic, not a trigger

The engine computes **Conditional Value-at-Risk** — the average loss in the worst slice of outcomes — from today's regime-matched neighbors, using the general-distribution estimator (Rockafellar–Uryasev) that behaves correctly on a discrete empirical sample. CVaR is a more honest tail-risk number than plain VaR because it averages across the whole tail rather than reading a single percentile.

CVaR is computed each cycle and persisted, but it is **never a live trigger** — it is operator instrumentation only. The estimate carries genuine uncertainty: a kNN pool at a small tail yields only a handful of distinct tail observations, so the dashboard treats the value as a discussion prompt, not a forecast. A stronger "CVaR-divergence detector" idea was deliberately **not** built: comparing two CVaR windows only relocates the same small-sample problem rather than escaping it. A fail-safe invariant guarantees that an absent CVaR estimate can never itself cause a breach signal.

### 7.7 CRRA-EU utility (the autotuner's objective)

When the autotuner ranks parameter sets, it does not maximize raw average return — that ignores risk. It maximizes **Constant-Relative-Risk-Aversion expected utility**: each daily return is converted to a utility score that is concave, so a loss costs more than an equal-sized gain is worth. The shape is set by the risk-aversion parameter **γ**; Planet Stopper's default is moderately risk-averse, appropriate for a capital-preservation overlay. The selection statistic is a one-sample t-statistic on the per-day utilities — "is this configuration's risk-adjusted experience distinguishable from luck?" The wealth-argument floor is applied to the *input* only, never to the output utility, so the t-statistic cannot be inflated. (References: Pratt 1964; Merton 1969; Samuelson 1969.)

### 7.8 The layered exit-priority resolution

The six feeding computations (volatility scaling, time-squeeze, parabolic ratchet, breakeven, the two VWAP layers, and the MC gate) collapse asymmetrically into **four** exit triggers. `resolve_trigger_priority` is a pure function that selects the single canonical winner by a fixed order and reports every co-fired trigger:

| Priority | Trigger | Catches |
|----------|---------|---------|
| 1 (highest) | **VWAP Breakdown** | A sharp liquidity event / regime-shift — the fastest hard cut. |
| 2 | **Take-Profit** | An exceptional upside the regime is unlikely to sustain. |
| 3 | **VWAP Bleed Cut** | A slow erosion below VWAP. |
| 4 (floor) | **Trailing Stop** | Everything else — the slowest, momentum-respecting catch-all. |

The defensible ordering principle is *fastest hard cut first, slowest momentum-respecting cut last.* Reporting all co-fired triggers (not just the winner) preserves the conviction signal a single combined number would discard.

---

## 8. Optimization: the Optuna autotuner

The autotuner re-fits each symphony's parameters from history and refuses to deploy a fit it cannot statistically distinguish from luck. It runs weekly (Friday / weekend) after the EOD post-mortem.

### The walk-forward

For each symphony, the autotuner:

1. **Loads 125 trading days** of 1-minute history from the local Alpaca cache.
2. **Splits 60% train / 20% validation / 20% frozen-eval**, applying a *purge* and a one-day *embargo* at each fold boundary so the rolling-volatility window cannot leak across the split (the López de Prado anti-leakage discipline).
3. **Validates NN1 compliance** (see below) at module load and at entry, with default-deny on any unrecognized freeze discipline.
4. **Runs 500 Optuna trials** with the TPE sampler. The sampler concentrates the search on promising regions, which induces dependence between trials.
5. **Scores each trial** by its CRRA-EU utility t-statistic on the validation window and selects the best.
6. **Applies the overfitting haircut** (next subsection). If the best trial clears the adjusted bar it is certified; otherwise the autotuner deploys **nothing** and the prior parameters carry over.
7. **Scores the certified winner once** on the frozen-eval fold — the honest post-selection metric the operator sees. The frozen-eval fold is consumed exactly once per cycle; no peeking.
8. **Writes the three observer advisors' observations** (§[6.3](#63-the-observer-producers-advisors)).

### The overfitting haircut

Run 500 random parameter sets and the *best* of them will look better than it deserves — by luck alone. This is the multiple-testing problem and it is the central failure of "I backtested N strategies and picked the winner." Planet Stopper corrects for it with a **Benjamini-Hochberg-Yekutieli (BHY)** haircut, which raises the bar a candidate must clear in proportion to how many tests were effectively run (the Yekutieli factor handles the dependence the TPE sampler introduces). If the winner does not clear the raised bar, no parameters deploy.

A **researcher-degrees-of-freedom** counter feeds the same bar: if a developer manually tried variants offline and recorded them, those count toward the effective test count too, so the test cannot be gamed by hand-pre-filtering. The accounting is additive — the effective count is the Optuna trial count plus the recorded researcher degrees of freedom.

This is the **same acceptance gate the AI Advisor reuses** (`acceptance_gate.py`): every advisor proposal is screened through it, with the candidate count standing in for the trial count.

### NN1 spec-freeze — honest provenance for every constant

Every parameter carries a **freeze discipline** recording *why* it has its value. There is a fixed set of honest disciplines (theory-derived, operator/regulatory mandate, replicated empirical regularity, bootstrap block-length selection, operational cadence, and calibration-without-an-optimization-target) and exactly one **banned** discipline: choosing a value *because the backtest's P&L liked it*. The autotuner refuses to start if any frozen parameter carries the banned discipline or an unrecognized one.

This makes "I picked this number because the backtest liked it" structurally unrepresentable. If a developer tried to slip a P&L-selected constant in, it would inflate the researcher-DoF counter (raising the haircut bar), and if they hid it, the spec-bundle hash would mismatch and the autotuner would refuse to run.

### What is tuned vs. frozen

| | Examples |
|--|----------|
| **Tuned by Optuna** | Trailing-stop multipliers and floors, parabolic velocity/squeeze, VWAP-bleed multiplier and tick counts, VWAP-cross band, MC arming threshold, take-profit MC threshold. |
| **Frozen by THEORY** | The risk-aversion γ, the utility family (CRRA), and the wealth argument. Never touched by Optuna. |
| **Frozen by calibration / stylized fact** | The 20-day volatility window, the time-squeeze curve, and the walk-forward window/ratio. |

### A note on the data window

The 125-day window is short by published walk-forward standards, and after purge the validation and frozen-eval folds are only a handful of usable days each. The math is sound; the calibration window is statistically thin. The autotuner acknowledges this in code, and the frozen-eval t-statistic should be read with a wide error bar. When the math cannot honestly distinguish the day's winner from luck, **nothing deploys** — and that refusal is itself the operator-trust mechanism, visible on the dashboard with the haircut statistics.

---

## 9. Integrations

| Service | Used for | Credentials |
|---------|----------|-------------|
| **Composer.trade** | Reading symphony holdings, running stateless inline backtests for the AI Advisor, and (in live mode) liquidating to cash via its API, with exponential-backoff retries. | `COMPOSER_KEY_ID`, `COMPOSER_SECRET`, plus per-account UUIDs (`ACCOUNT_INDIVIDUAL` / `ACCOUNT_ROTH` / `ACCOUNT_TRAD`) |
| **Alpaca** | 1-minute historical and intraday price bars for the underlying ETFs. | `ALPACA_KEY`, `ALPACA_SECRET` |
| **Discord** | Exit alerts and the daily EOD post-mortem, with QuickChart-rendered summaries. | `DISCORD_WEBHOOK_URL` |
| **Anthropic (optional)** | The config advisor's suggestions and the explain-only chat (§[6.2](#62-the-config-advisor-ai_advisorpy)). Not needed to run the daemon. | `ANTHROPIC_API_KEY` |

> Composer's API is poorly documented and is assumed to drift. Treat any change to the Composer client as requiring fresh verification against the live API.

---

## 10. Data model

Two SQLite databases (§[3](#3-architecture)). The **state DB** schema is built from numbered, additive SQL migrations under `migrations/` (`001_*.sql` onward, applied in declared order). Migration discipline is **additive-first**: new columns are NULLable with a DEFAULT and changes are never destructive in a single step, so a migration can always be applied to a live database.

What the state DB holds, at a glance:

- **Live state & decisions** — per-symphony bot state, exit-trigger telemetry (winner plus every co-fired flag), and the daily shadow-history used for Guard-Alpha comparison.
- **Diagnostics** — the per-cycle CVaR diagnostic and the Monte-Carlo regime-match telemetry (match distance and whether the MC veto was suppressed).
- **Optimization records** — autotune-run summaries with their selection and frozen-eval statistics.
- **Provenance & advisors** — spec bundles and facets (with their freeze disciplines), the researcher-degrees-of-freedom ledger, and advisor observations keyed by symphony (including the AI Advisor's gated swap and logic-change proposals).
- **Operator config** — algorithm parameters and per-account settings.

> One migration is intentionally applied out of strict numeric order. This is deliberate and documented inline; reordering it would corrupt live databases that already applied it.

---

## 11. Running it

### Prerequisites

- Python 3.12 (the project targets 3.12; 3.11+ should work).
- A Composer.trade account with API credentials and at least one deployed symphony.
- An Alpaca account with API credentials for 1-minute data.
- A Discord webhook URL for alerts.
- (Optional) An Anthropic API key to enable the AI Advisor's LLM suggestions and chat.
- A machine reachable during US market hours (09:30–16:00 ET).

### Install

```bash
git clone <repository>
cd AlphaBotPM
python -m venv .venv
.venv/Scripts/activate          # Windows; on Unix: source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # for running the test suite / linter
```

### Configure (`.env`)

The daemon is configured via a `.env` file (many values are also editable from the dashboard's settings panel). Core keys:

```text
COMPOSER_KEY_ID=...
COMPOSER_SECRET=...
ACCOUNT_INDIVIDUAL=uuid          # one or more accounts; any subset may be set
ACCOUNT_ROTH=uuid
ACCOUNT_TRAD=uuid
ALPACA_KEY=...
ALPACA_SECRET=...
DISCORD_WEBHOOK_URL=...
ANTHROPIC_API_KEY=...           # optional; enables AI Advisor suggestions + chat

LIVE_EXECUTION=False            # leave False until dry-run validation is done
EXECUTION_START_TIME=09:30
```

Tunable algorithm parameters (the autotuner overrides these once it has run; defaults shown):

```text
TRIGGER_THRESHOLD_PCT=15.0      # MC arming ceiling; 2x is the disarm level
TAKE_PROFIT_MC_PCT=5.0          # MC floor below which take-profit arms
VWAP_CROSS_HWM_PCT=1.0
PARABOLIC_VELOCITY_THRESHOLD=2.0
VWAP_OPEN_WINDOW_GRACE_MINUTES=15
SECOND_WINDOW_CVAR_ENABLED=0    # leave off; enables the Divergence Explainer
```

### Run

```bash
python app.py
```

This starts the Flask dashboard on `http://localhost:5000` (overridable via the `PORT` env var) and the minute scheduler. To confirm it is healthy: the bot-status badge reads active, your deployed symphonies appear in the table within a minute, and the next-tick countdown decrements.

### Dry-run vs. live

- `LIVE_EXECUTION=False` (paper) — evaluates every cycle and posts Discord alerts as if trading, but never calls Composer's liquidation endpoint. Use it for at least two weeks against your live symphonies.
- `LIVE_EXECUTION=True` — live; exits trigger real liquidations against Composer. `is_live=True` is always explicit and never a default.

### Graceful shutdown

Use `Ctrl+C` in the terminal running `python app.py` (the cleanest path), or the managed `restart.ps1` on Windows. Avoid hard kills: on Windows a forced kill bypasses cleanup and leaves the SQLite WAL needing a checkpoint on next start. The daemon recovers automatically on restart, but a graceful shutdown is preferred.

### Project skills

The repository ships operator/developer skills for common tasks, including:

| Skill | Purpose |
|-------|---------|
| `/run-tests` | Run the pytest suite with the project's default exclusions. |
| `/lint` | Run `ruff` format-check and lint (auto-fix safe issues). |
| `/backtest` | Replay the risk engine over a historical range from saved state and produce a P&L + exit-decision log. |
| `/db-inspect` | Read-only SQLite query helper for both databases. |
| `/api-fixture` | Capture a live Composer/Alpaca response to a versioned JSON test fixture. |
| `/discord-test` | Send a probe alert through the Discord + QuickChart pipeline. |
| `/perf-snapshot` | Compare live performance against the no-Planet-Stopper counterfactual (Guard Alpha). |
| `/optuna-compare` | Diff two autotune runs — parameter shifts, objective deltas, and what drove them. |
| `/symphony-diff` | Compare two symphonies head-to-head. |

### Operator runbooks

`docs/runbooks/` covers common operational scenarios: diagnosing Composer API rejection loops, resolving missing IANA tzdata on a host, and resetting the Optuna study database after calibration-shifting changes.

---

## 12. Testing

Tests live under `tests/`, organized by surface (`engine`, `math_engine`, `autotuner`, `app`, `ai_advisor`, `analytics`, `database`, `reporting`, `synthetic_history`, and more). The suite is large — hundreds of test files and thousands of test functions.

- **Default run** (`/run-tests`) — exercises the engine, math, autotuner, advisor, dashboard, and analytics suites. It deselects live, slow, and performance tests by default (pytest markers).
- **Live integration** — tests marked `live` hit real APIs and are opt-in; a few skip when local credentials are absent.
- **Slow / property** and **performance** tests are similarly opt-in via their markers.

Math-layer changes are held to a hard standard: every change to a math layer requires a golden-fixture test, every API call must be reproducible from a fixture, and several invariants are pinned — the exit-priority resolver's output for every flag combination, the Monte-Carlo seed determinism across restarts, the haircut output for a canonical search, and the advisor read-only wall.

The repository uses `pyproject.toml` for the `ruff` and `pytest` configuration. (A GitHub Actions test harness is on the roadmap.)

---

## 13. Safety boundaries

The system's guarantees, gathered in one place:

- **The AI surface is advise-only.** Neither the config advisor, the proposal suite, nor the observer advisors ever act on the operator's behalf. The proposal suite only reads from and backtests against Composer — it never places, mutates, or cancels a trade — and every survivor is applied by the operator, by hand, in Composer. Chat is explain-only with no write path.
- **Live execution is explicit.** `is_live=True` / `LIVE_EXECUTION=True` is always an explicit setting, never a default. Paper mode is the default.
- **The dashboard cannot trade.** It is read-only for live trades — enforced by architecture rule, by read-only SQLite connections, and by a disabled manual-trigger endpoint. The single deliberate trade surface is the explicit "sell account" panic button.
- **No blocking I/O on the execution or request path.** The engine runs on a one-minute cadence and the dashboard reads from a cache; neither blocks on a live API call. The AI Advisor's backtest-and-gate pipeline runs strictly offline and is never imported on the live execution path.
- **Fail safe, not fail open.** When the Monte-Carlo gate cannot produce a trustworthy estimate (insufficient history *or* an unprecedented regime), it returns a sentinel and the protective trailing stop still fires on its own. When CVaR is absent, it can never cause a breach. NaN/Inf inputs are rejected at the math boundary rather than silently swallowed.
- **The two databases never cross-join.** A corrupt optimization study cannot poison live state.
- **Parameters are honestly provenanced.** The autotuner refuses to deploy a parameter set chosen because a backtest liked it, and refuses to deploy any winner it cannot statistically distinguish from luck. The same acceptance gate screens every AI Advisor proposal.

---

## 14. What Planet Stopper does NOT do

- It does **not open positions** — entry is Composer's job.
- It does **not size positions** — sizing is Composer's job.
- It does **not make alpha calls** — there is no master forecast of expected return. It says only "now is the time to exit *this* symphony to cash."
- It does **not produce a portfolio-level decision** — every decision is per symphony.
- It does **not use CVaR as a trigger** — CVaR is a diagnostic only, and there is no CVaR-divergence signal.
- It does **not expose a manual force-trigger** on the dashboard — the scheduler is the only legal engine spawner.
- It does **not auto-apply AI suggestions or proposals** — every suggestion, swap, and logic change is operator-reviewed and applied by hand in Composer. The AI Advisor never touches your live account.

---

*Disclaimer: Planet Stopper is an automated execution tool. Algorithmic trading carries significant risk. Always validate parameters in dry-run mode before enabling `LIVE_EXECUTION`.*
