# AlphaBot v3

## Summary

The primary intent of **AlphaBot** is to function as an institutional-grade, algorithmic risk engine that sits on top of Composer.trade portfolios (referred to as "symphonies"). Rather than relying on passive "buy-and-hold" strategies that leave capital exposed to intraday market crashes, AlphaBot actively monitors live market data minute-by-minute. Its goal is to dynamically calculate intelligent trailing stops and automatically execute "sell-to-cash" orders via API when mathematical risk thresholds are breached. Ultimately, it seeks to generate "Guard Alpha"—mathematically proving that its automated early exits saved the user money compared to holding the asset until the market close.

**Hard fork status:** AlphaBot v3 is a hard fork of the upstream Composer bot — it never re-syncs upstream. All architecture decisions are made with full autonomy within project guidelines.

---

## Features Overview

AlphaBot achieves its goals through a sophisticated combination of data ingestion, multi-layered mathematical defense protocols, concurrency management, and machine learning optimization.

### **Live Data Ingestion & Regime Detection**
* Alpaca API Integration: Fetches real-time, 1-minute historical and live pricing data for all active holdings across user portfolios. It utilizes parallel processing and local caching to rapidly generate synthetic intraday history.
* SPY-Conditioned Macro Environment: Filters the historical Monte Carlo dataset to only use days that closely match today's SPY performance. It uses a Nearest Neighbors matching algorithm based on SPY daily returns and rolling 20-day volatility to preserve cross-asset correlations.
  *(Note: Legacy VIX Macro-Awareness has been explicitly removed in favor of Volatility-Scaled limits)*.


### **The Multi-Layered Risk Engine**
**Volatility Scaling**
* Calculates an active trailing stop distance based strictly on the portfolio's 20-day volatility.
* **Logarithmic Time Squeeze:** Shrinks the trailing stop distance smoothly and predictably based on the time of day using a logarithmic decay curve. The dynamic multiplier decays from 1.5x at the open to 0.5x by the close.
* **Parabolic Squeeze Ratchet:** Measures tick-by-tick return velocity. If the velocity exceeds the `PARABOLIC_VELOCITY_THRESHOLD`, the engine permanently ratchets the trailing stop tighter using the `MAX_PARABOLIC_SQUEEZE` multiplier to protect the peak.
* **Risk Guard (Breakeven Lock):** To lock the absolute downside floor to breakeven (0.0%), the live return must hold above a dynamically calculated activation threshold (clamped between 0.4% and 3.0%) for 5 consecutive ticks.
* **Monte Carlo State Engine:** Runs thousands of vectorized Monte Carlo simulations to calculate the probability of the symphony beating its current return. It dictates state-switching by arming defensive trailing stops when the probability falls below the `TRIGGER_THRESHOLD_PCT` and triggering take-profit traps when it falls below the `TAKE_PROFIT_MC_PCT`.
* **Volatility-Scaled VWAP Defenses:** Implements a dual-system VWAP defense. System A (VWAP Breakdown) forces exits if the portfolio price drops below its VWAP after hitting a high-water mark. System B (VWAP Bleed Cut) dynamically calculates a stop floor using a `VWAP_BLEED_MULTIPLIER` applied to the asset's 20-day volatility, safely clamped between -0.50% and -3.0%, to amputate bleeding assets without being whipsawed by noise.
* **Strict Exit Confirmation:** Standard trailing stops require 3 consecutive ticks below the stop line (with a 0.10% magnitude floor) AND a Monte Carlo sanity gate check (probability under 60.0) to prevent premature exits on market noise.


### **Symphony-Level Database Architecture**
* **SQLite State Management:** Uses a highly concurrent SQLite database to store states, isolated risk parameters, execution locks, and continuous chart histories.
* **Symphony-Level Strategies:** Maintains independent parameter tuning and variable locks based on unique, normalized symphony names. Decision math is symphony-level only — port-level decision math was deprecated in Sprint 3 (Stream A). Port state display surfaces are retained (AX-2 badge helpers, restart_notice), but no autonomous port-level decision logic remains in production code.
* **Per-Symphony Activity Logging:** Captures and stores specific event logs (e.g., arming, triggers, execution) for each symphony into a local `symphony_logs.json` file, ensuring intraday actions are auditable.


### **Automated Execution & Alerting**
* **Gatekeeper & Scheduler:** A fully internal Flask-based daemon process using the `schedule` library runs the bot every minute during market hours, removing reliance on external cron jobs.
* **Composer API Trigger:** Fires a POST request to Composer's backend, liquidating a symphony to cash if the stop level is hit. It utilizes an exponential backoff retry mechanism (1, 2, 4, 10 seconds) to ensure resilience against rate limits (HTTP 429) and network spikes.
* **Discord Webhooks (Multi-Embed):** Instantly sends a clean, multi-embed payload detailing the exit reason, Guard Alpha metrics, VWAP stats, and a summary chart powered by QuickChart. It chunks the Discord messages into batches of 10 to strictly adhere to Discord's rate limits.


### **EOD Autotuning & Post-Mortem Analytics**
* **Two-Stage EOD Pipeline:** Generates a daily post-mortem JSON snapshot using a two-stage process to prevent Composer API cash flatlines from corrupting the math. Stage 1 locks true shadow returns and Guard Alpha using live Alpaca pricing precisely at 15:53 ET. Stage 2 runs at 16:00 ET to inject tomorrow's target holdings without overwriting the previously locked math.
* **Persistent Optimization Engine:** Performs a 125-trading-day Walk-Forward Analysis using an 80% Train / 20% Out-of-Sample test split. Powered by Optuna with a persistent SQLite backend, it runs 500 parallel trials per unique symphony name to tune dynamic stops, multipliers, and parabolic thresholds. The autotuner objective is CRRA-EU (`compute_crra_eu_tstat = mean(U)/(sd(U)/√T)`) with risk-aversion shaping — replacing the legacy Sortino-like constant. N-effective additive accounting (`N_effective = N_optuna + S`) preserves BHY haircut integrity across spec facets.
* **AI Advisor integration:** Post-walk-forward, `autotuner.py` invokes the Phase-1 Advisor producers — Overfitting Conscience, Spec Critic, and Divergence Explainer — and writes their observations to `advisor_observations` via `insert_advisor_observation`. Overfitting Conscience reads `prior_runs` via `advisor_ro_query` (structural wall: `COALESCE(fold_role,'') != 'frozen_eval'`).


### **AI Advisor Producers**
Three Phase-1 Advisor producers are operational as of Sprint 3 (Stream B). They run post-walk-forward in `autotuner.py` and write to the `advisor_observations` table, keyed by `symphony_id`.

* **Overfitting Conscience** (`advisors/overfitting_conscience.py`): Reads `researcher_dof_ledger` + `spec_bundles`. Writes observations when `S > 0` (BACKTEST_SELECTION count) or other overfitting indicators are detected. Receives `prior_runs` via `advisor_ro_query` (frozen-eval wall enforced).
* **Spec Critic** (`advisors/spec_critic.py`): Reviews `spec_bundles` for completeness, missing facets, and freeze-discipline gaps. Writes verdicts.
* **Divergence Explainer** (`advisors/divergence_explainer.py`): Only fires when the `SECOND_WINDOW_CVAR_ENABLED` feature flag is active. When enabled, writes per-cycle divergence explanations for both CVaR windows. When disabled, writes a `NOT_APPLICABLE` observation. **No signed-divergence number is persisted or displayed** — the CVaR-divergence REJECT wall is intact.
* **Narrator**: Deferred to a future cycle. Phase-1 has no parameter drift to narrate.

The `/ai-advisor` UI (`templates/ai_advisor.html`) displays `advisor_observations` rows for the selected symphony (or globally if no filter). Columns shown: `advisor_role`, `subject_type`, `subject_id`, `verdict`, `timestamp`, `is_advisory_only` badge. `raw_response` is Jinja2-escaped.


### **Interactive Control Center (UI/UX)**
* **Live Dashboard:** A real-time Flask command center to view the exact distance to the stop level, status ranks, EOD shadow returns, and active EOD EOD chart data.
* **Settings Control Panel:** A dedicated API endpoint and UI structure to update `.env` globals and SQLite symphony strategies on the fly without restarting the application.
* **Manual Overrides:** Includes API triggers to force an immediate run, force an EOD analysis computation, force a Discord push, or manually trigger an immediate account liquidation to cash.



---

## Variables Explanation

The bot's operation is customized through various variables set in the `.env` file and managed via the web Settings panel. Symphony-specific parameters can be isolated and tuned independently.

### API Keys and Identifiers

* **`COMPOSER_KEY_ID`** & **`COMPOSER_SECRET`**: Authentication credentials for the Composer API.
* **`ACCOUNT_UUIDS`**: A comma-separated list of your Composer Account UUIDs.
* **`ALPACA_KEY`** & **`ALPACA_SECRET`**: Alpaca API credentials used to fetch real-time and historical market data.
* **`DISCORD_WEBHOOK_URL`**: The Discord webhook URL where the bot will send execution alerts and post-mortem reports.

### Master Control

* **`LIVE_EXECUTION`**: A boolean switch (`True`/`False`). Set to `False` to run the bot in paper/simulation mode (Safe). Set to `True` to allow the bot to send live "sell-to-cash" requests (Danger).
* **`EXECUTION_START_TIME`**: The time (e.g., `09:30`) when the bot begins monitoring and calculating live stops.

### Algorithm Parameters

* **`TRIGGER_THRESHOLD_PCT`**: The primary Monte Carlo threshold (e.g., 15.0) that triggers the initial "Trailing Stop" arming.
* **`TAKE_PROFIT_MC_PCT`**: The target Monte Carlo probability threshold (e.g., 5.0) to activate aggressive "Take Profit" arming on exceptional gains.
* **`MAX_SQUEEZE_FLOOR`**: The absolute tightest the stop distance can shrink during peak logarithmic decay.
* **`VWAP_CROSS_HWM_PCT`**: The return threshold an asset must hit to activate the VWAP Breakdown (System A) logic.
* **`VWAP_BLEED_MULTIPLIER`**: The dynamic multiplier applied to a symphony's 20-day volatility to establish its maximum VWAP Bleed Cut threshold.
* **`VWAP_BLEED_TICKS`**: The number of consecutive ticks required below the calculated bleed threshold before liquidating (System B).
* **`PARABOLIC_VELOCITY_THRESHOLD`**: The threshold of upward return velocity required to trigger the permanent "Parabolic Squeeze" ratchet.
* **`MAX_PARABOLIC_SQUEEZE`**: The stop squeeze multiplier applied continuously once the Parabolic Squeeze is armed or the breakeven lock is achieved.
* **`SECOND_WINDOW_CVAR_ENABLED`**: Feature flag for the optional second-window CVaR operator surface. When enabled, Divergence Explainer writes per-cycle explanations. Default: disabled.

---

## Installation Guide

1. **Clone the repository and enter the directory**
```
git clone https://github.com/Jope31/AlphaBot.git
cd AlphaBot

```
2. **Create and activate a virtual environment (Recommended)**<br>
This keeps the bot's dependencies isolated from your system Python.
* **On Mac/Linux:**
```
python3 -m venv venv
source venv/bin/activate
```

* **On Windows:**
```
python -m venv venv
venv\Scripts\activate
```

3. **Configure the Environment Variables:**<br>
Create or open the `.env` file and input your specific credentials:
* Add your Composer Key, Secret, and Account UUIDs.
* Add your Alpaca Key and Secret.
* Paste your Discord Webhook URL (how to: https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).
* Adjust initial global algorithm parameters as needed.<br>*These are also editable from the "Edit Variables" window on the Dashboard.*
```
COMPOSER_KEY_ID=
COMPOSER_SECRET=
ACCOUNT_UUIDS=
ALPACA_KEY=
ALPACA_SECRET=
DISCORD_WEBHOOK_URL=
LIVE_EXECUTION=False
EXECUTION_START_TIME=09:30
```

4. **Initialize the Database:**
The bot uses SQLite databases for state management and optimization persistence. Ensure the script has read/write permissions in its directory so it can automatically manage `alphabot_state.db` and `optuna_studies.db`.
6. **Run the Application:**
Start the Flask server and background scheduler by running:
```
python app.py

```


Navigate to the local server address (`http://127.0.0.1:5000`) in your browser to view the interactive live dashboard, view per-symphony logs, and configure settings.

---

## Operator Runbooks

Procedures for common operational scenarios:

- [Composer Rejection Diagnostic](docs/runbooks/composer-rejection-diagnostic.md) — diagnosing and resolving Composer API rejection loops
- [tzdata Missing on Host](docs/runbooks/tzdata-missing-on-host.md) — resolving `ZoneInfoNotFoundError` on hosts without IANA tzdata
- [Optuna Recalibration](docs/runbooks/optuna-recalibration.md) — resetting the Optuna study DB after calibration-shifting code changes

---

## Architecture Notes

- **Math engine constants:** All numeric constants in `math_engine.py` are named and documented. The codebase has zero unnamed numeric literals in the math layer. Provenance for every constant is tracked in [docs/math_engine/constants.md](docs/math_engine/constants.md).
- **CRRA-EU autotuner objective (Phase-1 M1):** The autotuner objective is `compute_crra_eu_tstat(U) = mean(U)/(sd(U)/√T)` where `U` is CRRA utility applied to each simulated return. `WEALTH_ARG_FLOOR` is a named constant applied to the **input wealth argument W** (never to the output utility). The legacy Sortino-like objective is fully replaced as of Sprint 2.
- **CVaR diagnostic (Phase-1 M2):** `compute_portfolio_cvar` computes expected shortfall at the 5th percentile off the existing kNN pool. The result (`CVaRAssessment`) is a diagnostic instrument: it carries a stderr on the distinct-tail-observation count (~7–8, not the resample count), a `tail_obs_count` field, and a mandatory "diagnostic, not a signal — do not trade on this" + bias-warning label. CVaR is **not a live exit trigger** in Phase 1.
- **NN1 spec-freeze discipline:** All spec facets frozen by THEORY/MANDATE/STYLIZED_FACT are enforced at autotuner entry via `validate_nn1_compliance`. `BACKTEST_SELECTION` freeze discipline is forbidden for these facets. Violations raise at entry — never silently proceed.
- **N-effective additive accounting:** `compute_n_effective` adds `N_optuna` and the researcher DOF ledger `S` count additively (`N_effective = N_optuna + S`). Both `_haircut_select` call sites wire this. Yekutieli c(N) is preserved.
- **Advisor wall:** The `advisor_ro_query` structural wall (`COALESCE(fold_role,'') != 'frozen_eval'` filter) prevents read-through to frozen-eval rows. `query_wall_breach_tripwire` enforces this in tests. Violation surfaces before the advisory result is consumed.
- **Port-level deprecation (Sprint 3):** Decision math is symphony-level only. `engine/multi_cycle.py`, `engine/port_selector.py`, and `engine/port_aggregator.py` have been deleted. `engine/exit_authority.py` survives as display-only badge helpers (`SITE-D1` + `AX-2`). `engine/dual_altitude.py` is deleted. No autonomous port-level decision logic remains in production code. Port state schema rows are preserved (additive-first policy).
- **Test harness:** 3753 tests / 0 failures / 5 expected-fails / 5 skipped at Sprint 3 tip (be74f4f). Live-execution tests excluded by default, opt in via `--include-live`. Run via `/run-tests` skill.
- **Schema migrations:** 25 migration SQL files (001–025) in `migrations/`. `_MIGRATION_FILES` in `database.py` applies 004–025; migrations 001–003 are applied unconditionally in `init_db`. Migration 021 is listed before 020 intentionally — see ARCH-002 inline comment in `database.py`. Migration 025 adds `advisor_observations.symphony_id` (NULLable, additive). Apply in order before starting the daemon after a schema-affecting upgrade.
- **Invariants enforced in the math layer:** Trailing-stop monotonicity is enforced inside `compute_active_trailing_stop` via the `previously_persisted_stop_level` kwarg (Fu & Zhang canonical clamp). NaN/Inf inputs are rejected at the boundary of 11 math functions — callers receive a raised `ValueError`, never a silent sentinel. The MC sentinel (`MC_INSUFFICIENT_HISTORY_SENTINEL = None`) is out-of-band; the protective stop always fires on ticks-below-stop alone when sentinel is active.
- **Decision-Science roadmap:** Feature plans live in `feature-plans/decision-science/`. The scaffold (`plan/finalist-a-scaffold` branch) covers Phase 1 (HARDEN-core), Phase 1.5 (M3 re-derivation), Phase 2 (evidence-gated Finalist B), and engine-audit lanes. See `feature-plans/decision-science/README.md` for the full index. Cross-cycle audit reports live in `docs/audit/`.

---

*Disclaimer: AlphaBotNext is an automated execution tool. Algorithmic trading carries significant risk. Always test parameters in Dry Run mode before enabling `LIVE_EXECUTION`.*
