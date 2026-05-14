"""Flask application for AlphaBot Control Center with Account-Level settings."""

import os
import sys
import io
import time
import threading
import subprocess
from datetime import datetime
import schedule
import requests
import logging
from flask import Flask, render_template, jsonify, request
from dotenv import dotenv_values, set_key

import database
import analytics
import ai_advisor

# Minimum observations before quantstats metrics are deemed statistically
# meaningful for the dashboard.  Below this floor the route surfaces
# `insufficient_history=True` so the UI can render a "not enough history yet"
# banner instead of misleadingly precise but underpowered numbers.
_PERFORMANCE_MIN_HISTORY_DAYS = 30
_PERFORMANCE_VALID_SCOPES = ("aggregate", "symphony")
_PERFORMANCE_METRIC_KEYS = (
    "total_return",
    "annualized_return",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "win_rate",
)
_PERFORMANCE_NONE_METRICS = {k: None for k in _PERFORMANCE_METRIC_KEYS}

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

app = Flask(__name__)
# Reload .html templates on every request without restarting the process.
# NOTE: use_reloader / debug auto-restart are intentionally NOT enabled — the
# process owns a minute-scheduler that spawns real-money execution subprocesses;
# a Python-code restart would interrupt live ops.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

COMPOSER_BASE_URL = "https://api.composer.trade/api/v0.1"

# --- 1. Bot Execution Logic ---
def trigger_alpha_bot(force=False):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Triggering Alpha Bot...")
    try:
        cmd = [sys.executable, "alpha_bot_execution.py"]
        if force:
            cmd.append("--force")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Execution failed: {e}")

def threaded_trigger():
    threading.Thread(target=trigger_alpha_bot, daemon=True).start()

def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    while True:
        schedule.run_pending()
        time.sleep(1)

# --- 2. Web Dashboard Routes ---
@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    try:
        state_data = database.load_state()
        if not state_data:
            return jsonify({"status": "waiting", "message": "Bot state initializing."})

        env_vars = dotenv_values(".env")
        live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

        next_run_seconds = 0
        valid_jobs = [job for job in schedule.get_jobs() if job.next_run]
        if valid_jobs:
            delta = min(job.next_run for job in valid_jobs) - datetime.now()
            next_run_seconds = max(0, int(delta.total_seconds()))

        # Render HTML for UI
        symphony_keys = [k for k in state_data.keys() if isinstance(state_data[k], dict)]
        accounts_map = {}
        for k in symphony_keys:
            sym = state_data[k]
            acc_id = sym.get("account", "Unknown Account")
            if acc_id not in accounts_map:
                accounts_map[acc_id] = []
            sym["id"] = k
            sym["normalized_name"] = database.normalize_name(sym.get("name", ""))
            accounts_map[acc_id].append(sym)
            
        account_labels = {}
        acc_ind = env_vars.get("ACCOUNT_INDIVIDUAL", "").strip()
        acc_roth = env_vars.get("ACCOUNT_ROTH", "").strip()
        acc_trad = env_vars.get("ACCOUNT_TRAD", "").strip()
        
        if acc_ind: account_labels[acc_ind] = "Individual"
        if acc_roth: account_labels[acc_roth] = "Roth IRA"
        if acc_trad: account_labels[acc_trad] = "Trad. IRA"

        # Sorting logic
        sort_col = request.args.get("sortCol", "name")
        sort_dir = request.args.get("sortDir", "asc")
        is_desc = (sort_dir == "desc")

        def get_status_rank(s):
            if s.get("triggered"):
                if s.get("triggered_reason") == "VWAP Breakdown": return 5
                return 4
            if s.get("para_armed"): return 3
            if s.get("tp_armed"): return 2
            if s.get("armed"): return 1
            return 0

        def get_exit_ret(s):
            if s.get("triggered"):
                return s.get("triggered_at_return") if s.get("triggered_at_return") is not None else (s.get("current_return") or -999.0)
            return s.get("current_return") if s.get("current_return") is not None else -999.0

        for acc_id in accounts_map:
            if sort_col == "mc_prob":
                accounts_map[acc_id].sort(key=lambda s: s.get("mc_prob") if s.get("mc_prob") is not None else -999.0, reverse=is_desc)
            elif sort_col == "status":
                accounts_map[acc_id].sort(key=get_status_rank, reverse=is_desc)
            elif sort_col == "stop_level":
                accounts_map[acc_id].sort(key=lambda s: s.get("triggered_at_stop") if s.get("triggered") and s.get("triggered_at_stop") is not None else (s.get("stop_trigger") if s.get("stop_trigger") is not None else -999.0), reverse=is_desc)
            elif sort_col == "current_return":
                accounts_map[acc_id].sort(key=get_exit_ret, reverse=is_desc)
            elif sort_col == "shadow_hwm":
                accounts_map[acc_id].sort(key=lambda s: s.get("shadow_hwm", -999.0), reverse=is_desc)
            elif sort_col == "shadow":
                accounts_map[acc_id].sort(key=lambda s: s.get("current_return") if s.get("current_return") is not None else -999.0, reverse=is_desc)
            else: # name
                accounts_map[acc_id].sort(key=lambda s: (s.get("name") or s.get("id", "")).lower(), reverse=is_desc)

        # Build symphonies list for M1 analytics helpers from bot_state.
        # Fields derived: last_percent_change from current_return/100, value from current_value.
        # CR/MDD fallback to 0 when not stored in bot_state — helpers still need the fields.
        symphonies_list = []
        for k in symphony_keys:
            s = state_data[k]
            cr = s.get("current_return") or 0.0
            val = s.get("current_value") or 0.0
            symphonies_list.append({
                "id": k,
                "value": val,
                "last_percent_change": cr / 100.0,
                "simple_return": s.get("simple_return", 0.0),
                "net_deposits": s.get("net_deposits", 0.0),
                "time_weighted_return": s.get("time_weighted_return", 0.0),
                "max_drawdown": s.get("max_drawdown", 0.0),
            })

        # Attach per-symphony TC/CR/MDD to each sym dict so the template can render them.
        for k in symphony_keys:
            s = state_data[k]
            sym_dict = next((d for d in symphonies_list if d["id"] == k), {})
            try:
                s["_tc"] = analytics.get_symphony_today_change(sym_dict, s)
            except (KeyError, TypeError, ValueError):
                s["_tc"] = {"if_held": 0.0, "dry_run": 0.0}
            try:
                s["_cr"] = analytics.get_symphony_cumulative_return(sym_dict, s)
            except (KeyError, TypeError, ValueError):
                s["_cr"] = {"if_held": 0.0, "dry_run": 0.0}
            try:
                s["_mdd"] = analytics.get_symphony_max_drawdown(sym_dict, s)
            except (KeyError, TypeError, ValueError):
                s["_mdd"] = {"if_held": 0.0, "dry_run": 0.0}

        portfolio_strip = {
            "today_change": analytics.get_portfolio_today_change(symphonies_list, state_data),
            "cumulative_return": analytics.get_portfolio_cumulative_return(symphonies_list, state_data),
            "max_drawdown": analytics.get_portfolio_max_drawdown(symphonies_list, state_data),
        }

        data_as_of = datetime.now().strftime("%H:%M ET")

        rendered_html = render_template("table_partial.html", accounts_map=accounts_map, account_labels=account_labels, sort_col=sort_col, sort_dir=sort_dir, data_as_of=data_as_of)

        return jsonify({
            "status": "active",
            "state": state_data,
            "live_mode": live_mode,
            "execution_start_time": env_vars.get("EXECUTION_START_TIME", "09:30"),
            "next_run_seconds": next_run_seconds,
            "html": rendered_html,
            "portfolio_strip": portfolio_strip,
            "data_as_of": data_as_of,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/logs/<symphony_id>")
def api_symphony_logs(symphony_id):
    try:
        logs = database.get_symphony_logs(symphony_id)
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/chart/<symphony_id>")
def get_chart_data(symphony_id):
    try:
        chart_data = database.load_chart_history()
        symphony_data = chart_data.get("symphonies", {}).get(symphony_id, [])
        return jsonify({"status": "success", "data": symphony_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/trigger", methods=["POST"])
def manual_trigger():
    threading.Thread(target=trigger_alpha_bot, args=(True,)).start()
    return jsonify({"status": "success", "message": "Bot execution forced."})

@app.route("/api/force_eod", methods=["POST"])
def force_eod():
    try:
        from datetime import datetime, timedelta
        bot_state = database.load_state()
        chart_history = database.load_chart_history()
        prev_date_str = chart_history.get("date")
        if not prev_date_str:
            prev_date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        env_vars = dotenv_values(ENV_FILE_PATH)
        acc_ind = env_vars.get("ACCOUNT_INDIVIDUAL", "").strip()
        acc_roth = env_vars.get("ACCOUNT_ROTH", "").strip()
        acc_trad = env_vars.get("ACCOUNT_TRAD", "").strip()
        account_uuids = [uid for uid in [acc_ind, acc_roth, acc_trad] if uid]
        discord_webhook = env_vars.get("DISCORD_WEBHOOK_URL", "")

        def run_eod_tasks():
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Forcing EOD Analysis for {prev_date_str}...")
            import reporting
            import autotuner
            reporting.generate_eod_snapshot(bot_state, prev_date_str, is_post_rebalance=False, discord_webhook_url=discord_webhook)
            reporting.generate_eod_snapshot(bot_state, prev_date_str, is_post_rebalance=True, discord_webhook_url=discord_webhook)
            autotuner_changes = autotuner.run_autotuner(bot_state, prev_date_str, account_uuids, is_forced=True)
            reporting.send_eod_discord_post(prev_date_str, f"post_mortem_{prev_date_str}.json", autotuner_changes, discord_webhook)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Forced EOD Analysis complete.")

        threading.Thread(target=run_eod_tasks, daemon=True).start()
        return jsonify({"status": "success", "message": "EOD Analysis initiated for " + prev_date_str})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/resend_discord", methods=["POST"])
def resend_discord():
    try:
        from datetime import datetime, timedelta
        chart_history = database.load_chart_history()
        prev_date_str = chart_history.get("date")
        if not prev_date_str:
            prev_date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        env_vars = dotenv_values(ENV_FILE_PATH)
        discord_webhook = env_vars.get("DISCORD_WEBHOOK_URL", "")

        def run_discord_push():
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Resending Discord Report for {prev_date_str}...")
            import reporting
            # Pass None for optimization_results to skip tuning and just send the current JSON
            reporting.send_eod_discord_post(prev_date_str, f"post_mortem_{prev_date_str}.json", None, discord_webhook)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Discord resend complete.")

        threading.Thread(target=run_discord_push, daemon=True).start()
        return jsonify({"status": "success", "message": "Discord push initiated for " + prev_date_str})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/history/<int:days>")
def get_history(days):
    import glob, json, os
    from datetime import datetime, timedelta
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    files = glob.glob("post_mortem_*.json")
    
    stats = {
        "total_alpha": 0.0,
        "total_saved": 0.0,
        "trigger_count": 0,
        "wins": 0,
        "by_reason": {}
    }
    
    for f_path in files:
        try:
            # Extract date from filename: post_mortem_YYYY-MM-DD.json
            date_part = f_path.replace("post_mortem_", "").replace(".json", "")
            file_date = datetime.strptime(date_part, "%Y-%m-%d")
            if start_date <= file_date <= end_date:
                with open(f_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for t in data.get("triggers", []):
                        alpha = t.get("saved_pct_guard_alpha", 0.0)
                        dollars = t.get("saved_dollars", 0.0)
                        reason = t.get("exit_reason", "Unknown")
                        
                        stats["total_alpha"] += alpha
                        stats["total_saved"] += dollars
                        stats["trigger_count"] += 1
                        if alpha > 0: stats["wins"] += 1
                        
                        if reason not in stats["by_reason"]:
                            stats["by_reason"][reason] = {"alpha": 0.0, "count": 0, "wins": 0}
                        stats["by_reason"][reason]["alpha"] += alpha
                        stats["by_reason"][reason]["count"] += 1
                        if alpha > 0: stats["by_reason"][reason]["wins"] += 1
        except: continue

    # Final Averages
    if stats["trigger_count"] > 0:
        stats["avg_guard_alpha"] = stats["total_alpha"] / stats["trigger_count"]
        stats["win_rate"] = (stats["wins"] / stats["trigger_count"]) * 100
    else:
        stats["avg_guard_alpha"] = 0
        stats["win_rate"] = 0
        
    return jsonify(stats)

# --- 2b. Performance Tab (DV2) ---
@app.route("/performance")
def performance_page():
    """Render the Performance tab (read-only operator surface).

    Pure render — no database mutation, no engine invocation, no network I/O.
    Client-side JS pulls /api/performance and /api/performance/symphonies on
    load and on scope/symphony changes.
    """
    return render_template("performance.html", min_history_days=_PERFORMANCE_MIN_HISTORY_DAYS)


@app.route("/api/performance")
def api_performance():
    """Performance time series + quantstats metrics.

    Query params:
        scope:        "aggregate" (default) or "symphony"
        days:         integer history window (default 60)
        symphony_id:  required when scope=symphony

    Response shape (binding — see tests/app/test_performance_routes.py):
        {
          "scope": "aggregate" | "symphony",
          "dates": [...],
          "live_returns": [...],
          "shadow_returns": [...],
          "live_metrics":   {7 documented keys},
          "shadow_metrics": {7 documented keys},
          "observation_count": int,
          "insufficient_history": bool
        }

    Read-only contract: this route never calls database.save_*, never calls
    database.acquire_lock(), and never issues a requests.post.  The live
    engine holds the SQLite lock at the top of every minute; coupling UI
    latency to that lock would let dashboard polls back up the execution
    loop.
    """
    scope = request.args.get("scope", "aggregate")
    if scope not in _PERFORMANCE_VALID_SCOPES:
        return jsonify({
            "status": "error",
            "message": (
                f"invalid scope {scope!r}; expected one of "
                f"{list(_PERFORMANCE_VALID_SCOPES)}"
            ),
        }), 400

    try:
        days = int(request.args.get("days", 60))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "days must be an integer",
        }), 400

    symphony_id = request.args.get("symphony_id")
    if scope == "symphony" and not symphony_id:
        return jsonify({
            "status": "error",
            "message": "symphony_id is required when scope=symphony",
        }), 400

    history = analytics.get_history_with_cache_invalidation(days=days)

    if scope == "aggregate":
        dates, live_returns, shadow_returns = analytics.compute_aggregate_returns(
            history
        )
    else:
        dates, live_returns, shadow_returns = analytics.compute_per_symphony_returns(
            history, symphony_id
        )

    observation_count = len(dates)
    insufficient_history = observation_count < _PERFORMANCE_MIN_HISTORY_DAYS

    if observation_count == 0:
        live_metrics = dict(_PERFORMANCE_NONE_METRICS)
        shadow_metrics = dict(_PERFORMANCE_NONE_METRICS)
    else:
        live_metrics = analytics.compute_quantstats_metrics(live_returns)
        shadow_metrics = analytics.compute_quantstats_metrics(shadow_returns)

    # Defensive float-cast so JSON serialization never trips over numpy/Decimal
    # types coming back from the aggregator.
    live_returns_out = [float(r) for r in live_returns]
    shadow_returns_out = [float(r) for r in shadow_returns]

    return jsonify({
        "scope": scope,
        "dates": list(dates),
        "live_returns": live_returns_out,
        "shadow_returns": shadow_returns_out,
        "live_metrics": live_metrics,
        "shadow_metrics": shadow_metrics,
        "observation_count": observation_count,
        "insufficient_history": insufficient_history,
    })


@app.route("/api/performance/symphonies")
def api_performance_symphonies():
    """Sorted list of symphony_ids present in the post-mortem history."""
    history = analytics.get_history_with_cache_invalidation()
    symphonies = analytics.list_available_symphonies(history)
    return jsonify({"symphonies": list(symphonies)})


# --- 3. Account Liquidation ---
def perform_account_liquidation(account_id, key, secret, live_mode):
    headers = {"x-api-key-id": key, "authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphony-stats-meta"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            for sym in resp.json().get("symphonies", []):
                if live_mode:
                    sell_url = f"{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{sym.get('symphony_id') or sym.get('id')}/go-to-cash"
                    sell_resp = requests.post(sell_url, headers=headers, json={}, timeout=10)
                    print(f"Liquidated {sym.get('name')} (HTTP {sell_resp.status_code})")
                    time.sleep(1.5)
    except Exception as e:
        print(f"Liquidation Error: {e}")

@app.route("/api/sell_account", methods=["POST"])
def sell_account():
    data = request.json
    account_id = data.get("account_id")
    env_vars = dotenv_values(".env")
    live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

    if not (account_id and env_vars.get("COMPOSER_KEY_ID")):
        return jsonify({"status": "error", "message": "Missing credentials or account ID."}), 400

    if not live_mode:
        # Real-money safety gate: never spawn the liquidation thread when
        # LIVE_EXECUTION is False.  Return an explicit dry-run signal so the
        # operator dashboard can distinguish a successful no-op from a real
        # execution.
        return jsonify({
            "status": "dry_run",
            "message": "Panic-stop disabled in non-LIVE mode. Set LIVE_EXECUTION=True to arm.",
            "live_mode": False,
            "executed": False,
        })

    threading.Thread(target=perform_account_liquidation, args=(account_id, env_vars.get("COMPOSER_KEY_ID"), env_vars.get("COMPOSER_SECRET"), live_mode)).start()
    return jsonify({
        "status": "success",
        "message": "Liquidation initiated.",
        "live_mode": True,
        "executed": True,
    })

# --- 4. Tabbed Settings / Control Panel Routes ---
@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Returns Globals from .env and Symphony Strategies from SQLite."""
    env_vars = dotenv_values(ENV_FILE_PATH)
    globals_data = {
        "LIVE_EXECUTION": env_vars.get("LIVE_EXECUTION", "False"),
        "EXECUTION_START_TIME": env_vars.get("EXECUTION_START_TIME", "09:30"),
        "COMPOSER_KEY_ID": env_vars.get("COMPOSER_KEY_ID", ""),
        "COMPOSER_SECRET": env_vars.get("COMPOSER_SECRET", ""),
        "ALPACA_KEY": env_vars.get("ALPACA_KEY", ""),
        "ALPACA_SECRET": env_vars.get("ALPACA_SECRET", ""),
        "ACCOUNT_INDIVIDUAL": env_vars.get("ACCOUNT_INDIVIDUAL", ""),
        "ACCOUNT_ROTH": env_vars.get("ACCOUNT_ROTH", ""),
        "ACCOUNT_TRAD": env_vars.get("ACCOUNT_TRAD", ""),
        "DISCORD_WEBHOOK_URL": env_vars.get("DISCORD_WEBHOOK_URL", ""),
        # Never echo the raw key — return empty string regardless of whether set.
        "ANTHROPIC_API_KEY": "",
    }

    # Fetch unique symphony names from the current bot_state
    state_data = database.load_state()
    symphony_names = set()
    for data in state_data.values():
        if isinstance(data, dict) and "name" in data:
            symphony_names.add(database.normalize_name(data["name"]))

    symphonies_data = {}
    for name in symphony_names:
        symphonies_data[name] = database.get_symphony_strategy(name)

    return jsonify({"globals": globals_data, "symphonies": symphonies_data})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    """Saves Globals to .env and Symphony Strategies to SQLite."""
    payload = request.json

    try:
        # Save Globals
        for key, val in payload.get("globals", {}).items():
            set_key(ENV_FILE_PATH, key, str(val))

        # Save Symphony Strategies
        for sym_name, strategy_data in payload.get("symphonies", {}).items():
            params = {k: float(v) for k, v in strategy_data.get("params", {}).items()}
            locked = strategy_data.get("locked_vars", [])
            database.save_symphony_strategy(sym_name, params, locked)

        return jsonify({"status": "success", "message": "Variables updated successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- 5. AI Advisor Routes ---
@app.route("/ai-advisor", methods=["GET"])
def ai_advisor_tab():
    """Render the Claude AI Config Advisor tab."""
    return render_template("ai_advisor.html")


@app.route("/ai-advisor/suggest", methods=["POST"])
def ai_advisor_suggest():
    """Call Claude advisor and return suggestions as JSON."""
    payload = request.json or {}
    symphony_id = payload.get("symphony_id", "")
    context = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=symphony_id)
    suggestions_response, error_msg = ai_advisor.request_suggestions(context)
    if error_msg is not None:
        return jsonify({"error": error_msg}), 200
    suggestions = [s.model_dump() for s in suggestions_response.suggestions]
    return jsonify({"suggestions": suggestions})


@app.route("/ai-advisor/accept", methods=["POST"])
def ai_advisor_accept():
    """Apply an accepted suggestion through all three C2 safety gates."""
    payload = request.json or {}
    symphony_id = payload.get("symphony_id", "")
    suggestion_data = payload.get("suggestion", {})

    suggestion_obj = ai_advisor.ConfigSuggestion(
        config_key=suggestion_data.get("config_key", ""),
        current_value=suggestion_data.get("current_value", 0),
        suggested_value=suggestion_data.get("suggested_value", 0),
        rationale=suggestion_data.get("rationale", ""),
        risk_direction=suggestion_data.get("risk_direction", "neutral"),
        confidence=suggestion_data.get("confidence", "medium"),
        data_sufficiency=suggestion_data.get("data_sufficiency", "sufficient"),
    )

    # C2 Gate 1: allowlist
    allowed, rejected = ai_advisor.enforce_suggestion_allowlist([suggestion_obj])
    if rejected:
        return jsonify({"status": "rejected", "error": "key not in allowlist"}), 200

    # C2 Gate 2: risk direction (log disagreement, do not block)
    ai_advisor.check_risk_direction_agreement(suggestion_obj)

    # C2 Gate 3: OOS revalidation — pass flat params, not the DB wrapper
    current_strategy_row = database.get_symphony_strategy(symphony_id) or {"params": {}, "locked_vars": []}
    flat_params = dict(current_strategy_row.get("params", {}))
    locked_vars = current_strategy_row.get("locked_vars", [])
    oos_result = ai_advisor.revalidate_suggestion_oos(
        symphony_id,
        suggestion_obj.config_key,
        suggestion_obj.suggested_value,
        flat_params,
    )
    if not oos_result["passed"]:
        return jsonify({"status": "rejected", "error": oos_result["detail"]}), 200

    # All gates passed — write the config change
    patched_params = dict(flat_params)
    patched_params[suggestion_obj.config_key] = suggestion_obj.suggested_value
    database.save_symphony_strategy(symphony_id, patched_params, locked_vars)
    return jsonify({"status": "accepted"})


@app.route("/ai-advisor/reject", methods=["POST"])
def ai_advisor_reject():
    """Record operator rejection — no config write."""
    return jsonify({"status": "rejected"})


if __name__ == "__main__":
    # Reconfigure stdout to UTF-8 so emoji/non-Latin-1 chars don't crash on
    # Windows (cp1252 default).  Guarded to __main__ so pytest's capture is
    # not affected when this module is imported during test collection.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Starting Alpha Bot Control Center at http://localhost:{port}\n")

    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=port, debug=False, use_reloader=False)
