"""SQLite state management for AlphaBot with Account-Level Strategies."""

import sqlite3
import json
import time
from datetime import datetime

DB_FILE = "alphabot_state.db"

# DEFAULT STRATEGY PARAMETERS (Used when a new account is detected)
DEFAULT_STRATEGY = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "MAX_SQUEEZE_FLOOR": 0.20,
    "VWAP_CROSS_HWM_PCT": 1.0,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.50,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 10
}

# By default, we lock the non-user-specified variables so BO only tunes the requested
DEFAULT_LOCKED_VARS = [
    "TRIGGER_THRESHOLD_PCT"
]

def get_connection():
    return sqlite3.connect(DB_FILE, timeout=10.0)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Execution & State Tracking
    cursor.execute("CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS chart_archive (date TEXT, symphony_id TEXT, data TEXT, UNIQUE(date, symphony_id))")
    
    # NEW: Symphony-Level Strategy Storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symphony_strategies (
            symphony_name TEXT PRIMARY KEY,
            parameters TEXT,
            locked_vars TEXT
        )
    """)

    cursor.execute("INSERT OR IGNORE INTO execution_lock (id, is_locked, timestamp) VALUES (1, 0, 0)")
    cursor.execute("INSERT OR IGNORE INTO bot_state (id, data) VALUES (1, '{}')")
    cursor.execute("INSERT OR IGNORE INTO chart_history (id, data) VALUES (1, '{}')")
    
    conn.commit()
    conn.close()

# --- Lock Management ---
def acquire_lock():
    conn = get_connection()
    cursor = conn.cursor()
    current_time = time.time()
    cursor.execute("SELECT is_locked, timestamp FROM execution_lock WHERE id = 1")
    row = cursor.fetchone()
    if row[0] == 1 and (current_time - row[1] < 60):
        conn.close()
        return False
    cursor.execute("UPDATE execution_lock SET is_locked = 1, timestamp = ? WHERE id = 1", (current_time,))
    conn.commit()
    conn.close()
    return True

def release_lock():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE execution_lock SET is_locked = 0 WHERE id = 1")
    conn.commit()
    conn.close()

# --- State Management ---
def load_state():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM bot_state WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return json.loads(row[0]) if row else {}

def save_state(state_dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_state SET data = ? WHERE id = 1", (json.dumps(state_dict),))
    conn.commit()
    conn.close()

def wipe_transient_state(state_dict):
    """Wipes transient state keys for all symphonies to prevent bleeding across sessions."""
    for s_id, s_data in state_dict.items():
        if isinstance(s_data, dict):
            s_data["high_water_mark"] = -999.0
            s_data["shadow_hwm"] = -999.0
            s_data["prev_return"] = 0.0
            s_data["armed"] = False
            s_data["tp_armed"] = False
            s_data["para_armed"] = False
            s_data["triggered"] = False
            s_data["breakeven_locked"] = False
            s_data["below_stop_count"] = 0
            s_data["above_tp_count"] = 0
            s_data["vwap_ticks"] = 0
            s_data["vwap_bleed_ticks"] = 0
            s_data["hwm_hold_ticks"] = 0
            s_data["mc_history"] = []
            
            # Remove any trigger-related snapshot data
            for k in ["triggered_reason", "triggered_at_return", "triggered_at_hwm", 
                      "triggered_at_stop", "triggered_at_time", "trigger_prices", 
                      "triggered_basket_snapshot"]:
                if k in s_data:
                    del s_data[k]
    return state_dict

# --- Chart History & Archive ---
def load_chart_history():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM chart_history WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return json.loads(row[0]) if row else {}

def save_chart_history(chart_dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE chart_history SET data = ? WHERE id = 1", (json.dumps(chart_dict),))
    conn.commit()
    conn.close()

def save_chart_archive(date_str, symphony_id, data):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO chart_archive (date, symphony_id, data) VALUES (?, ?, ?)", (date_str, symphony_id, json.dumps(data)))
    conn.commit()
    conn.close()

def get_rolling_60day_chart(current_date_str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM chart_archive ORDER BY date DESC LIMIT 60")
    dates = [row[0] for row in cursor.fetchall()]
    if not dates:
        conn.close()
        return {}
    placeholders = ",".join("?" * len(dates))
    cursor.execute(f"SELECT date, symphony_id, data FROM chart_archive WHERE date IN ({placeholders})", dates)
    history_60d = {}
    for row in cursor.fetchall():
        date, sym_id, data_json = row[0], row[1], row[2]
        if sym_id not in history_60d:
            history_60d[sym_id] = {}
        history_60d[sym_id][date] = json.loads(data_json)
    conn.close()
    return history_60d



def normalize_name(name):
    return name.strip().lower()

# --- Symphony Strategy Management (NEW) ---
def get_symphony_strategy(symphony_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT parameters, locked_vars FROM symphony_strategies WHERE symphony_name = ?", (symphony_name,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"params": json.loads(row[0]), "locked_vars": json.loads(row[1])}
    
    # Initialize with defaults if not found
    save_symphony_strategy(symphony_name, DEFAULT_STRATEGY, DEFAULT_LOCKED_VARS)
    return {"params": DEFAULT_STRATEGY.copy(), "locked_vars": DEFAULT_LOCKED_VARS.copy()}

def save_symphony_strategy(symphony_name, params, locked_vars):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO symphony_strategies (symphony_name, parameters, locked_vars) VALUES (?, ?, ?)",
        (symphony_name, json.dumps(params), json.dumps(locked_vars))
    )
    conn.commit()
    conn.close()

# --- Symphony Logging (NEW) ---
SYMPHONY_LOGS_FILE = "symphony_logs.json"

def get_symphony_logs(symphony_id):
    try:
        with open(SYMPHONY_LOGS_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
            return logs.get(symphony_id, [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def log_symphony_event(symphony_id, message, event_type="info"):
    logs = {}
    try:
        with open(SYMPHONY_LOGS_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
        
    if symphony_id not in logs:
        logs[symphony_id] = []
        
    timestamp = datetime.utcnow().isoformat() + "Z"
    logs[symphony_id].append({
        "timestamp": timestamp,
        "event_type": event_type,
        "message": message
    })
    
    try:
        with open(SYMPHONY_LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f)
    except Exception as e:
        print(f"Error saving symphony logs: {e}")

def clear_symphony_logs():
    try:
        with open(SYMPHONY_LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    except Exception as e:
        print(f"Error clearing symphony logs: {e}")

# Initialize tables on import
init_db()
