# AlphaBot v3

## Summary

The primary intent of **AlphaBot** is to function as an institutional-grade, algorithmic risk engine that sits on top of Composer.trade portfolios (referred to as "symphonies"). Rather than relying on passive "buy-and-hold" strategies that leave capital exposed to intraday market crashes, AlphaBot actively monitors live market data minute-by-minute. Its goal is to dynamically calculate intelligent trailing stops and automatically execute "sell-to-cash" orders via API when mathematical risk thresholds are breached. Ultimately, it seeks to generate "Guard Alpha"—mathematically proving that its automated early exits saved the user money compared to holding the asset until the market close.

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
* **Symphony-Level Strategies:** Maintains independent parameter tuning and variable locks based on unique, normalized symphony names.
* **Per-Symphony Activity Logging:** Captures and stores specific event logs (e.g., arming, triggers, execution) for each symphony into a local `symphony_logs.json` file, ensuring intraday actions are auditable.


### **Automated Execution & Alerting**
* **Gatekeeper & Scheduler:** A fully internal Flask-based daemon process using the `schedule` library runs the bot every minute during market hours, removing reliance on external cron jobs.
* **Composer API Trigger:** Fires a POST request to Composer's backend, liquidating a symphony to cash if the stop level is hit. It utilizes an exponential backoff retry mechanism (1, 2, 4, 10 seconds) to ensure resilience against rate limits (HTTP 429) and network spikes.
* **Discord Webhooks (Multi-Embed):** Instantly sends a clean, multi-embed payload detailing the exit reason, Guard Alpha metrics, VWAP stats, and a summary chart powered by QuickChart. It chunks the Discord messages into batches of 10 to strictly adhere to Discord's rate limits.


### **EOD Autotuning & Post-Mortem Analytics**
* **Two-Stage EOD Pipeline:** Generates a daily post-mortem JSON snapshot using a two-stage process to prevent Composer API cash flatlines from corrupting the math. Stage 1 locks true shadow returns and Guard Alpha using live Alpaca pricing precisely at 15:53 ET. Stage 2 runs at 16:00 ET to inject tomorrow's target holdings without overwriting the previously locked math.
* **Persistent Optimization Engine:** Performs a 125-trading-day Walk-Forward Analysis using an 80% Train / 20% Out-of-Sample test split. Powered by Optuna with a persistent SQLite backend, it runs 500 parallel trials per unique symphony name to tune dynamic stops, multipliers, and parabolic thresholds. It actively penalizes missed upside and peak-to-exit drawdowns. If the out-of-sample validation fails, the bot safely reverts to fallback parameters or global defaults.


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

*Disclaimer: AlphaBotNext is an automated execution tool. Algorithmic trading carries significant risk. Always test parameters in Dry Run mode before enabling `LIVE_EXECUTION`.*
