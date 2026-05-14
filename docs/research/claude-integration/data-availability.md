# Claude Integration: Quantitative Data Availability Audit
## AlphaBot v3 — Feature Feasibility Assessment

**Date:** 2026-05-14  
**Researcher:** Agent (File Search Specialist)  
**Objective:** Inventory all mathematical, quant, and symphony-definition data available to feed into a Claude prompt for "operator clicks button → Claude suggests config edits" feature.

---

## Executive Summary

**CRITICAL FINDING — Symphony Logic Availability: NOT AVAILABLE DIRECTLY, BUT SUBSTITUTABLE**

AlphaBot has **NO READ ACCESS** to a Composer symphony's decision-tree logic or rules (the actual "strategy definition"). The `GET /symphonies/{id}/score` endpoint documented in the Composer API *exists* in AlphaBot's dependencies and is **theoretically callable**, but AlphaBot **does not currently call it** — there is no code path to fetch or persist symphony logic. 

**What AlphaBot DOES have** for Claude is:
1. **Complete per-symphony runtime STATS** — returns, allocations, volatility, MC probabilities, VWAP signals, Guard Alpha metrics
2. **Complete internal strategy PARAMETERS** — all tuned decision thresholds (stored in `symphony_strategies` SQLite table and persisted across autotuner runs)
3. **Complete exit-decision AUDIT TRAIL** — why each position exited, P&L impact, reason codes (stored in `post_mortem_*.json`)
4. **Complete Optuna walk-forward OPTIMIZATION ARTIFACTS** — best parameters found, train/OOS scores, rejection reasons (in `optuna_studies.db`)

The feature **is feasible as originally scoped** because Claude's job is to suggest *parameter edits*, not understand symphony code. The inputs are: "Here's what the symphony did, here's what AlphaBot's math did in response, here's the tuned parameters, here's the performance delta." Symphony *logic* (if-this-then-that rules) is not necessary to make those suggestions.

---

## Part 1: Math & Quant Data Available

### 1.1 Math Engine Constants & Derivations

**Source:** `math_engine.py` — contains all algorithmic rules.

Per-symphony data (volatility, HWM, stop levels, MC probabilities, VWAP signals) is computed every minute during execution loop and stored in `bot_state[symphony_id]`.

Key metrics:
- 20-day historical volatility per symphony (from Alpaca 3-year cached history)
- High Water Mark (HWM) tracking — max return since armed
- Shadow HWM — tracks max even after triggered (for post-trigger Guard Alpha)
- Monte Carlo probability of beating benchmark (kNN regime-based simulation)
- VWAP deviation signals (allocation-weighted cross calculation)
- Active trailing stop distance with time-decay multiplier

### 1.2 Per-Symphony Runtime State in bot_state

Per-symphony persistent dictionary (`bot_state[symphony_id]`) updated every minute:

**Core Metrics:**
- `current_return` (%), `current_value` ($), `current_holdings` (list)
- `high_water_mark` (%), `shadow_hwm` (%)`
- `mc_prob` (%), `symphony_vol` (%)`
- `stop_trigger` (%), `active_stop_distance` (%)`

**State Machine (flags):**
- `armed`, `tp_armed`, `para_armed`, `triggered`, `breakeven_locked`

**Counters:**
- `below_stop_count`, `above_tp_count`, `vwap_ticks`, `vwap_bleed_ticks`, `hwm_hold_ticks`

**Triggered State (when exited):**
- `triggered_at_return`, `triggered_at_stop`, `triggered_at_hwm`, `triggered_at_time`
- `triggered_reason` (Trailing Stop | Take-Profit | VWAP Breakdown | VWAP Bleed Cut)
- `trigger_prices` (dict), `triggered_basket_snapshot` (list)

### 1.3 Post-Mortem JSON (`post_mortem_YYYY-MM-DD.json`)

Written at end-of-day by `reporting.py`. Contains:

- **Summary:** total_monitored, total_triggered, positive_guard_alpha_count
- **Per-trigger record:**
  - `symphony_name`, `account_id`, `symphony_value` ($)
  - `exit_reason` (string), `exit_return` (%), `attempted_trigger_level` (%)
  - `shadow_return` (%), `shadow_hwm` (%), `saved_pct_guard_alpha` (%), `saved_dollars` ($)
  - `hwm_at_trigger` (%), `symphony_vol` (%), `time_triggered`
  - `strategy_params` (current config at trigger time)
  - `next_day_holdings` (next rebalance composition)
- **Portfolio holdings:** `tomorrow_target_holdings` (aggregated allocation by ticker)

### 1.4 Optuna Optimization Artifacts

Stored in `optuna_studies.db` (SQLite RDB backend). Per symphony:

- **Best parameters:** all 7 tuned keys (TRIGGER_THRESHOLD_PCT, TAKE_PROFIT_MC_PCT, etc.)
- **Train score:** guard alpha % on 80% of 125-day history
- **OOS score:** guard alpha % on 20% validation set
- **Fallback/Default comparison:** scores for prior params and global defaults
- **Trial history:** all 500 trial results with params and scores

---

## Part 2: CRITICAL — Symphony Logic Availability

### Symphony Logic Definition: NOT AVAILABLE

The Composer API endpoint `/symphonies/{symphony-id}/score` returns the symphony's decision-tree logic (EDN-flavored JSON with rule nodes).

**AlphaBot status:** This endpoint is NOT called. Grep search confirms zero instances of `.../score` or `score_version` in codebase.

**Why not?** AlphaBot's feature does not need symphony logic. It needs to see:
- What the symphony currently holds
- How well its exits performed
- What parameters AlphaBot is using

**Symphony logic (the rules that define the symphony) is not needed** to suggest edits to AlphaBot's parameters.

### What Composer Returns: Only Stats, Not Logic

`GET /symphonies/{symphony-id}/symphony-stats-meta` returns:
- Holdings list with allocations
- Returns (simple, time-weighted)
- Sharpe, max drawdown
- Last rebalance date
- Next rebalance date
- Target tickers

This is OUTPUT (what was decided). The INPUT (how the decision was made) is in `/score` which AlphaBot does not fetch.

### Feasibility: FULLY FEASIBLE without Symphony Logic

Claude's task: "Suggest edits to AlphaBot's 7 tuned parameters based on observed Guard Alpha performance."

Claude does NOT need to know: "The symphony uses momentum reversion with Fourier decomposition."

Claude DOES need to know: "Last 7 days, Guard Alpha averaged +0.9%, mostly from Trailing Stop triggers."

Conclusion: Symphony logic is **NOT AVAILABLE**, but **NOT REQUIRED** for the feature.

---

## Part 3: Optuna Output & Tuned Config Persistence

### Strategy Parameters Table

`symphony_strategies` SQLite table stores per-symphony:

```
symphony_name (PRIMARY KEY) | parameters (JSON) | locked_vars (JSON)
```

7 tuned parameters (from Optuna):
- TRIGGER_THRESHOLD_PCT (5–25%)
- TAKE_PROFIT_MC_PCT (2–10%)
- VWAP_CROSS_HWM_PCT (0.5–2.5%)
- VWAP_BLEED_MULTIPLIER (0.5–3.0)
- VWAP_BLEED_TICKS (3–30)
- PARABOLIC_VELOCITY_THRESHOLD (1–4%)
- MAX_PARABOLIC_SQUEEZE (0.1–0.8)

Plus 1 locked var (TRIGGER_THRESHOLD_PCT by default — operator can override per symphony).

### Optuna Study Database

125-day rolling window, 80/20 train/OOS split:
- 500 trials per optimization
- Each trial: 7 parameter values, guard alpha score
- Best trial tracked (highest train score)
- OOS validation of best params vs fallback and default

---

## Part 4: Complete Data Inventory for Claude

| Category | Datum | Source | Persistent? |
|---|---|---|---|
| **Real-time stats** | Current return, volatility, HWM, MC prob | bot_state, math_engine | YES |
| **Position state** | Armed, triggered, reason, exit timestamp | bot_state | YES |
| **Exit quality** | Guard Alpha (%), saved dollars, shadow return | post_mortem JSON | YES |
| **Config tuned** | All 7 parameter values | symphony_strategies DB table | YES |
| **Optuna scores** | Train alpha, OOS alpha, fallback OOS, default OOS | optuna_studies.db | YES |
| **Holdings** | Tickers, allocations, prices | bot_state, Composer API | YES |
| **Historical deviation** | Avg slippage by exit reason (45-day rolling) | calculated in autotuner.py | YES |
| **Symphony logic rules** | Decision tree, if-then conditions | Composer /score endpoint | NO (not called) |

---

## Part 5: Gap Analysis

| Need | Have It? | Impact |
|---|---|---|
| Math/quant metrics | YES | Everything computable from bot_state + math_engine |
| Strategy parameters | YES | All 7 tuned thresholds in symphony_strategies table |
| Optuna performance context | YES | Train/OOS scores, best trial number, all trials queryable |
| Exit decision history | YES | 45-day post_mortem JSONs with reason codes and Guard Alpha |
| Symphony logic definition | NO | Not needed — Claude edits AlphaBot params, not symphony code |
| Execution slippage detail | PARTIAL | Post-trigger shadow return available, per-holding isolation not tracked |

**Feature feasibility: FULLY FEASIBLE.** Symphony logic is not blocking.

---

## Part 6: Recommended Data Package

When operator clicks "Get Claude's Suggestion," assemble:

```json
{
  "symphony_stats": {
    "name": "Tech Momentum",
    "value_usd": 123456.78,
    "current_return_pct": 3.45,
    "high_water_mark_pct": 5.20,
    "volatility_20d_pct": 1.82,
    "sharpe": 1.42,
    "max_drawdown_pct": 2.1,
    "holdings": [{"ticker": "AAPL", "allocation": 0.25}, ...]
  },
  "alphabot_config_current": {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    ...
  },
  "alphabot_config_last_tuned": {
    "date": "2026-05-10",
    "train_alpha_pct": 3.2,
    "oos_alpha_pct": 2.1,
    "fallback_oos_pct": 1.9
  },
  "guard_alpha_stats_45d": {
    "total_triggers": 18,
    "positive_count": 14,
    "avg_pct": 0.87,
    "by_reason": {"Trailing Stop": 0.92, "Take-Profit": 0.64, ...}
  }
}
```

---

## Conclusion

**Symphony Logic:** NOT AVAILABLE, NOT REQUIRED.

**Everything Else:** COMPLETELY AVAILABLE.

**Feature Status:** ✅ **FULLY FEASIBLE.** All necessary data for Claude to suggest parameter edits is queryable from bot_state, post_mortem JSONs, math_engine, and Optuna DB.