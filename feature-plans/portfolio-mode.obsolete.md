# Feature: Portfolio-Wide AlphaBot Mode (Cycle B)
Status: draft-research-pending
Created: 2026-05-15
Predecessor: feature-plans/ai-advisor-tuning.md (Cycle A — Tuning page)

## Summary

Introduce an operating-mode toggle on AlphaBot: **per-symphony** (today's behavior — each symphony has its own exit-criteria parameters) vs **portfolio-wide** (one set of exit-criteria parameters applied across all symphonies in the book). The toggle is exposed on the Tuning page (stub already present from Cycle A); flipping it changes how the engine reads parameters, how the autotuner optimizes, and how the AI Advisor assembles context. This is a substantive trading-strategy change, not a UI refactor — it's a different thesis about how AlphaBot should manage risk and must be informed by research before code is written.

## Why This Is Cycle B, Not Cycle A

Cycle A ships the UI surface and the AI-vs-Optuna side-by-side. Cycle B ships the underlying ability to *operate* AlphaBot in portfolio mode. Splitting is required because Cycle B:

1. **Touches `alpha_bot_execution.py` + `math_engine.py` read paths** — every `get_symphony_strategy(symphony_id)` call becomes mode-aware. The project's minute scheduler runs these on every tick during market hours; this is the live-ops hot path.
2. **Requires a new autotuner objective function** — today's `run_simulation` returns single-symphony guard-alpha. Portfolio mode needs an aggregate objective: value-weighted? equal-weighted? worst-case across symphonies? Sharpe of aggregate? This is a methodology decision, not an implementation detail.
3. **Requires real `scope="global"` AI Advisor logic** — the signature stub exists (`ai_advisor.py:343`) but the global-scope branch produces no aggregated context. Cross-symphony Optuna evidence, portfolio volatility regime, correlation structure all need prompt-engineering.
4. **Requires a schema decision with permanence consequences** — where do port-wide params live: `bot_state.data` top-level key, new `portfolio_strategy` table, or `symphony_strategies` row keyed by sentinel `__portfolio__`. Each has different migration cost and read-path cost.
5. **Is a different trading strategy**, not a UI affordance — whether portfolio mode is a *better* operating mode than per-symphony is a research question, not a foregone conclusion.

## Phase 0 — Research (BLOCKS Phase 1)

Dispatched in parallel before any code:

1. **`quant-risk-researcher`** — Literature + practitioner research on portfolio-level vs strategy-level risk-overlay management. Specific questions:
   - When does portfolio-wide trailing-stop outperform per-symphony trailing-stop?
   - How should parameters (squeeze floor, parabolic threshold, etc.) aggregate when applied at portfolio level — pick conservative bound, weighted average, regime-conditional?
   - Are there published frameworks for cross-symphony correlation feeding back into a portfolio-level exit decision?
   - What's the survivorship/regime-bias risk of porting per-symphony parameters tuned independently into a portfolio context?
   - Cite specific papers, dates, applicability caveats.

2. **`optuna-methodology-researcher`** — Statistical-validity research on portfolio-level walk-forward optimization. Specific questions:
   - For a single objective scoring a parameter set across N symphonies' 125-day replay, what's the appropriate aggregation function (mean, median, value-weighted, percentile, worst-case)?
   - How does sample size change — is 125 days × N symphonies effectively N times more data, or do correlations collapse the effective sample?
   - What sampler choice (TPE / CMA-ES / GP) is appropriate when the objective surface is multi-symphony aggregate?
   - Are there study-design patterns (multi-objective Pareto across `worst_symphony_alpha` vs `mean_symphony_alpha`)?
   - Cite specific Optuna patterns, financial-ML papers, dates.

3. **`composer-alpaca-integration` (read-only recon)** — Inventory all engine + math-engine sites that today call `get_symphony_strategy(symphony_id)`. Quantify the read-path change blast radius for mode-aware reads.

## Phase 1 — Design (BLOCKED on Phase 0)

After research returns, PM presents:
- **Schema proposal** — where port-wide params live (recommendation with tradeoffs).
- **Read-path proposal** — single mode-aware accessor `get_active_strategy(symphony_id)` that internally branches on a top-level `bot_state.operating_mode` field. All call sites migrate to the accessor in one mechanical sweep.
- **Autotuner objective proposal** — based on Phase 0 research. Likely a flag on `run_autotuner` that switches between per-symphony loop (today) and a single portfolio-aggregate study.
- **AI Advisor context proposal** — concrete prompt design for `scope="global"`: aggregated Optuna evidence, portfolio volatility regime, what context to include and exclude.
- **Migration plan** — switching modes mid-trading-day is forbidden; gate on EOD-only mode change.

Gate-1 + Gate-2 dispatch only after design is approved.

## Phase 2 — Implementation (BLOCKED on Phase 1)

Tentative Quad+1: `quant-test-writer` + `implementer` + `quant-code-reviewer` + `risk-engine-specialist` (owns the read-path change in execution + math engine) + `optuna-specialist` (owns the portfolio-aggregate objective). `flask-dashboard-specialist` consults on enabling the mode toggle that Cycle A stubbed.

## Open Design Questions (will surface after Phase 0)

- **OQ-B1:** Mode toggle granularity — global single toggle, or per-account toggle (Individual/Roth/Trad each pick their own mode)?
- **OQ-B2:** What happens to per-symphony `symphony_strategies` rows when mode is portfolio-wide — preserved (so switching back is lossless) or archived?
- **OQ-B3:** Can the AI Advisor suggest *switching the mode itself*, or only suggest values within the current mode?
- **OQ-B4:** EOD-only mode switch, or allow intraday switch with cash-out gate?
- **OQ-B5:** Autotuner cadence — same Friday-night cadence as today, or portfolio-mode warrants different cadence (one study is faster than N studies)?

## Scope Boundaries

- **IN (Cycle B):**
  - Operating-mode toggle (per-symphony / portfolio-wide) wired through engine, autotuner, AI Advisor
  - New schema slot for portfolio-wide parameters
  - Portfolio-level autotuner objective
  - `ai_advisor.assemble_advisor_context(scope="global")` real implementation
  - Tuning page mode-toggle stub from Cycle A becomes functional

- **OUT (this cycle):**
  - Per-account mode mixing (deferred — OQ-B1)
  - Multi-strategy ensemble (per-symphony AND port-wide running simultaneously with arbitration)
  - Mode auto-selection (AI deciding which mode is better)

## Status

Awaiting Cycle A merge before research dispatch.
