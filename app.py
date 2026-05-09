"""Flask application for AlphaBot Control Center with Account-Level settings."""

import os
import sys
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

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

app = Flask(__name__)
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
            elif sort_col == "high_water_mark":
                accounts_map[acc_id].sort(key=lambda s: s.get("shadow_hwm", -999.0), reverse=is_desc)
            elif sort_col == "shadow":
                accounts_map[acc_id].sort(key=lambda s: s.get("current_return") if s.get("current_return") is not None else -999.0, reverse=is_desc)
            else: # name
                accounts_map[acc_id].sort(key=lambda s: (s.get("name") or s.get("id", "")).lower(), reverse=is_desc)

        rendered_html = render_template("table_partial.html", accounts_map=accounts_map, account_labels=account_labels, sort_col=sort_col, sort_dir=sort_dir)

        return jsonify({
            "status": "active",
            "state": state_data,
            "live_mode": live_mode,
            "execution_start_time": env_vars.get("EXECUTION_START_TIME", "09:30"),
            "next_run_seconds": next_run_seconds,
            "html": rendered_html
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

# --- 3. Account Liquidation ---
def perform_account_liquidation(account_id, key, secret, live_mode):
    headers = {"x-api-key-id": key, "authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphony-stats-meta"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            for sym in resp.json().get("symphonies", []):
                if live_mode:
                    sell_url = f"{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{sym.get('symphony_id', sym['id'])}/go-to-cash"
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

    if account_id and env_vars.get("COMPOSER_KEY_ID"):
        threading.Thread(target=perform_account_liquidation, args=(account_id, env_vars.get("COMPOSER_KEY_ID"), env_vars.get("COMPOSER_SECRET"), live_mode)).start()
        return jsonify({"status": "success", "message": "Liquidation initiated."})
    return jsonify({"status": "error", "message": "Missing credentials or account ID."}), 400

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

if __name__ == "__main__":
    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("\n🚀 Starting Alpha Bot Control Center at http://localhost:5000\n")
    
    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=5000, debug=False, use_reloader=False)
