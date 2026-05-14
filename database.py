"""SQLite state management for AlphaBot with Account-Level Strategies."""

import sqlite3
import json
import time
from datetime import datetime, timezone

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

    # P1: Per-run Optuna validation metrics — durable audit trail for Claude context-assembly
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS autotune_runs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp      TEXT    NOT NULL,
            symphony_id        TEXT    NOT NULL,
            oos_alpha          REAL    DEFAULT NULL,
            train_alpha        REAL    DEFAULT NULL,
            baseline_decision  TEXT    DEFAULT NULL,
            fallback_oos_alpha REAL    DEFAULT NULL,
            default_oos_alpha  REAL    DEFAULT NULL
        )
    """)

    # P3: Immutable append-only audit trail for Claude AI Config Advisor suggestions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_suggestions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT    NOT NULL,
            created_at          TEXT    NOT NULL,
            symphony_name       TEXT    NOT NULL,
            operator_identity   TEXT    NOT NULL DEFAULT '',
            prompt_inputs       TEXT    NOT NULL DEFAULT '{}',
            model_id            TEXT    NOT NULL DEFAULT '',
            generation_settings TEXT    NOT NULL DEFAULT '{}',
            raw_response        TEXT    NOT NULL DEFAULT '{}',
            validation_results  TEXT    NOT NULL DEFAULT '{}',
            param_name          TEXT    NOT NULL DEFAULT '',
            operator_decision   TEXT    NOT NULL DEFAULT 'pending',
            decision_at         TEXT    DEFAULT NULL,
            operator_note       TEXT    DEFAULT NULL,
            before_value        TEXT    DEFAULT NULL,
            after_value         TEXT    DEFAULT NULL,
            oos_revalidation    TEXT    DEFAULT NULL
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
    # NOTE: timestamp is intentionally not reset — preserved so the 60s stale-expiry
    # at acquire_lock still works correctly if a future code path inspects the
    # release-time gap. See tests/database/test_lock_lifecycle.py.
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
    symphony_name = normalize_name(symphony_name)
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
    symphony_name = normalize_name(symphony_name)
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
        
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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

# --- Autotune Run Persistence (P1) ---

def save_autotune_run(run_timestamp, symphony_id, oos_alpha, train_alpha,
                      baseline_decision, fallback_oos_alpha, default_oos_alpha) -> None:
    """Persist one row of per-run Optuna validation metrics to autotune_runs.

    Called once per symphony per run_autotuner() invocation, after baseline_decision
    is finalized.  All metric columns are NULLable so partial data never fails an
    INSERT (though callers should supply all seven values).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO autotune_runs
            (run_timestamp, symphony_id, oos_alpha, train_alpha,
             baseline_decision, fallback_oos_alpha, default_oos_alpha)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_timestamp, symphony_id, oos_alpha, train_alpha,
         baseline_decision, fallback_oos_alpha, default_oos_alpha),
    )
    conn.commit()
    conn.close()


def get_latest_autotune_run(symphony_id) -> dict | None:
    """Return the most-recent autotune_runs row for symphony_id as a dict.

    Returns None if no rows exist for that symphony — callers (e.g. Claude
    context-assembly) treat None as "Optuna has not yet run for this symphony".
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT run_timestamp, symphony_id, oos_alpha, train_alpha,
               baseline_decision, fallback_oos_alpha, default_oos_alpha
        FROM autotune_runs
        WHERE symphony_id = ?
        ORDER BY run_timestamp DESC
        LIMIT 1
        """,
        (symphony_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "run_timestamp":      row[0],
        "symphony_id":        row[1],
        "oos_alpha":          row[2],
        "train_alpha":        row[3],
        "baseline_decision":  row[4],
        "fallback_oos_alpha": row[5],
        "default_oos_alpha":  row[6],
    }


# --- LLM Suggestions Audit Trail (P3) ---

def record_llm_suggestion(
    *,
    session_id: str,
    created_at: str,
    symphony_name: str,
    operator_identity: str,
    prompt_inputs: dict,
    model_id: str,
    generation_settings: dict,
    raw_response: dict,
    validation_results: dict,
    param_name: str,
    operator_decision: str,
    decision_at: str | None = None,
    operator_note: str | None = None,
    before_value=None,
    after_value=None,
    oos_revalidation: dict | None = None,
) -> int:
    """Insert one append-only LLM suggestion audit record.

    Returns the new row id (AUTOINCREMENT surrogate key).  Dict-typed params are
    JSON-serialized on write so large blobs survive round-trips without truncation.
    This is the only write path for llm_suggestions — there is no update or delete
    accessor; the audit trail is immutable by design.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO llm_suggestions (
            session_id, created_at, symphony_name, operator_identity,
            prompt_inputs, model_id, generation_settings, raw_response,
            validation_results, param_name, operator_decision,
            decision_at, operator_note, before_value, after_value, oos_revalidation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            created_at,
            symphony_name,
            operator_identity,
            json.dumps(prompt_inputs),
            model_id,
            json.dumps(generation_settings),
            json.dumps(raw_response),
            json.dumps(validation_results),
            param_name,
            operator_decision,
            decision_at,
            operator_note,
            json.dumps(before_value) if before_value is not None else None,
            json.dumps(after_value) if after_value is not None else None,
            json.dumps(oos_revalidation) if oos_revalidation is not None else None,
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def _parse_llm_suggestion_row(row: tuple, columns: list[str]) -> dict:
    """Convert a raw llm_suggestions tuple into a fully-typed dict.

    JSON-blob columns (prompt_inputs, generation_settings, raw_response,
    validation_results, oos_revalidation, before_value, after_value) are
    deserialized from their stored TEXT representation so callers always receive
    the original Python objects, not raw JSON strings.
    """
    JSON_COLUMNS = {
        "prompt_inputs",
        "generation_settings",
        "raw_response",
        "validation_results",
        "oos_revalidation",
        "before_value",
        "after_value",
    }
    result = {}
    for col, val in zip(columns, row):
        if col in JSON_COLUMNS and val is not None:
            result[col] = json.loads(val)
        else:
            result[col] = val
    return result


_LLM_SUGGESTION_COLUMNS = [
    "id", "session_id", "created_at", "symphony_name", "operator_identity",
    "prompt_inputs", "model_id", "generation_settings", "raw_response",
    "validation_results", "param_name", "operator_decision", "decision_at",
    "operator_note", "before_value", "after_value", "oos_revalidation",
]


def get_suggestions_for_symphony(symphony_name: str) -> list[dict]:
    """Return all llm_suggestions rows for a given symphony, oldest-first.

    Returns an empty list if no rows exist — never raises for an unknown symphony.
    Each element is a full-column dict with JSON-blob columns deserialized.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT " + ", ".join(_LLM_SUGGESTION_COLUMNS) +
        " FROM llm_suggestions WHERE symphony_name = ? ORDER BY id ASC",
        (symphony_name,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_llm_suggestion_row(row, _LLM_SUGGESTION_COLUMNS) for row in rows]


def get_suggestions_for_session(session_id: str) -> list[dict]:
    """Return all llm_suggestions rows for a given session_id.

    Returns an empty list if no rows exist — never raises for an unknown session.
    Each element is a full-column dict with JSON-blob columns deserialized.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT " + ", ".join(_LLM_SUGGESTION_COLUMNS) +
        " FROM llm_suggestions WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_llm_suggestion_row(row, _LLM_SUGGESTION_COLUMNS) for row in rows]


# Initialize tables on import
init_db()
