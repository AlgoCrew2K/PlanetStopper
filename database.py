"""SQLite state management for AlphaBot with Account-Level Strategies."""

import logging
import os
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
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp       TEXT    NOT NULL,
            symphony_id         TEXT    NOT NULL,
            oos_alpha           REAL    DEFAULT NULL,
            train_alpha         REAL    DEFAULT NULL,
            baseline_decision   TEXT    DEFAULT NULL,
            fallback_oos_alpha  REAL    DEFAULT NULL,
            default_oos_alpha   REAL    DEFAULT NULL,
            deflated_sharpe     REAL    DEFAULT NULL,
            naive_sharpe        REAL    DEFAULT NULL,
            validation_sharpe   REAL    DEFAULT NULL,
            frozen_eval_sharpe  REAL    DEFAULT NULL
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
    run_migrations()


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
            s_data["prev_return"] = None  # sentinel: cycle-1 velocity = 0 (prevents false PARA-ARM on opening gap)
            s_data["armed"] = False
            s_data["tp_armed"] = False
            s_data["para_armed"] = False
            s_data["triggered"] = False
            s_data["breakeven_locked"] = False
            s_data["stop_trigger"] = None  # AC-E2.5: new position must not inherit prior position's stop floor
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
                      baseline_decision, fallback_oos_alpha, default_oos_alpha,
                      deflated_sharpe=None, naive_sharpe=None,
                      validation_sharpe=None, frozen_eval_sharpe=None) -> None:
    """Persist one row of per-run Optuna validation metrics to autotune_runs.

    Called once per symphony per run_autotuner() invocation, after baseline_decision
    is finalized.  All metric columns are NULLable so partial data never fails an
    INSERT (though callers should supply all values).

    O2 additions:
      deflated_sharpe: DSR value for the AI-branch best trial (Bailey & López de Prado 2014).
                       None when the fallback or default cascade was used instead.
      naive_sharpe:    Raw Optuna best trial Sortino before DSR correction. None for non-AI rows.

    O6 additions:
      validation_sharpe:  Sortino on the validation fold (20% of history); the metric used for
                          trial selection. Selection truth; visible to operator for audit.
      frozen_eval_sharpe: Sortino on the frozen-eval fold (final 20% of history); consumed once
                          post-selection for honest performance reporting (López de Prado 2018 Ch. 7.4).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO autotune_runs
            (run_timestamp, symphony_id, oos_alpha, train_alpha,
             baseline_decision, fallback_oos_alpha, default_oos_alpha,
             deflated_sharpe, naive_sharpe, validation_sharpe, frozen_eval_sharpe)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_timestamp, symphony_id, oos_alpha, train_alpha,
         baseline_decision, fallback_oos_alpha, default_oos_alpha,
         deflated_sharpe, naive_sharpe, validation_sharpe, frozen_eval_sharpe),
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
               baseline_decision, fallback_oos_alpha, default_oos_alpha,
               deflated_sharpe, naive_sharpe
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
        "deflated_sharpe":    row[7],
        "naive_sharpe":       row[8],
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


# --- H1: Schema Migration Runner ---

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

# Ordered list of numbered migration files to apply to alphabot_state.db.
# Append new entries here; never reorder or remove existing entries.
_MIGRATION_FILES = [
    "004_schema_migrations_tracker.sql",
    "005_exit_triggers.sql",
    "006_autotune_runs_sharpe.sql",
    "007_autotune_runs_frozen_eval.sql",
    "008_shadow_history.sql",
    "009_fleet_alert_state.sql",
]


def run_migrations() -> None:
    """Apply any pending numbered migrations to alphabot_state.db.

    Idempotent: schema_migrations tracker prevents re-applying a migration that
    has already been recorded.  Safe to call on every daemon startup.
    """
    conn = get_connection()
    # Ensure the tracker table itself exists before we try to query it.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.commit()

    for migration_name in _MIGRATION_FILES:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_name = ?",
            (migration_name,),
        ).fetchone()
        if row is not None:
            continue  # already applied

        migration_path = os.path.join(_MIGRATIONS_DIR, migration_name)
        try:
            with open(migration_path, "r", encoding="utf-8") as fh:
                sql = fh.read()
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (migration_name,),
            )
            conn.commit()
        except Exception as exc:
            logging.error("run_migrations: failed to apply %s: %s", migration_name, exc)

    conn.close()


# --- R1: Fleet Alert State Helpers ---


def read_fleet_alert() -> "dict | None":
    """Return the fleet_alert_state row as a dict, or None when the table is empty."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, tripped_at_et, triggered_reason, tripped_count, active_count, dismissed_at_et "
            "FROM fleet_alert_state WHERE id = 1"
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return dict(row)


def write_fleet_alert(payload: dict) -> None:
    """Upsert the fleet_alert_state singleton row (id=1).

    Clears dismissed_at_et to NULL unless the caller explicitly includes it in payload.
    Uses INSERT OR REPLACE so each call is idempotent.
    """
    dismissed_at_et = payload.get("dismissed_at_et", None)
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO fleet_alert_state "
        "(id, tripped_at_et, triggered_reason, tripped_count, active_count, dismissed_at_et) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (
            payload["tripped_at_et"],
            payload["triggered_reason"],
            payload["tripped_count"],
            payload["active_count"],
            dismissed_at_et,
        ),
    )
    conn.commit()


def clear_fleet_alert() -> None:
    """Delete the fleet_alert_state singleton row. Idempotent — safe when table is empty."""
    conn = get_connection()
    conn.execute("DELETE FROM fleet_alert_state WHERE id = 1")
    conn.commit()


# --- H1: Trigger Attribution Telemetry ---


def record_exit_trigger(
    *,
    symphony_id: str,
    account_id: str | None,
    triggered_reason: str,
    at_return: float | None,
    gate_state: dict | None,
    cycle_id: str | None,
) -> None:
    """Write one exit-trigger telemetry row.

    Opens its own connection — does NOT join the cycle's save_state transaction.
    A failure here must never fail the cycle; any exception is logged at ERROR
    and swallowed.  Called from alpha_bot_execution.py at the triggered=True set site.
    """
    from datetime import timedelta

    now_utc = datetime.now(timezone.utc)
    ts_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    # ET offset approximation for display; EDT = UTC-4.
    ts_et = (now_utc - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    gate_state_json = json.dumps(gate_state) if gate_state is not None else None

    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.execute(
            "INSERT INTO exit_triggers "
            "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, gate_state_json, cycle_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_utc,
                ts_et,
                symphony_id,
                account_id,
                triggered_reason,
                at_return,
                gate_state_json,
                cycle_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.error("record_exit_trigger failed for %s: %s", symphony_id, exc)


# --- M1F: Shadow-equity history ---

# In-memory cache of (symphony_id, trading_day) → cumulative shadow return.
# Invalidated when a new shadow_history row is written for that day.
_shadow_cr_cache: dict[tuple[str, str], float] = {}


def record_shadow_observation(
    *,
    symphony_id: str,
    account_id: "str | None",
    cycle_id: "str | None",
    ts_utc: str,
    ts_et: str,
    trading_day: str,
    current_return: float,
    shadow_return: float,
    is_post_trigger: int,
    trigger_id: "int | None",
) -> None:
    """Write one shadow-equity telemetry row (M1F).

    Opens its own connection — does NOT join the cycle's save_state transaction.
    Failure is logged at ERROR and swallowed; the cycle must never fail on telemetry.
    AC-M1F.1.2, PA-M1F-10: both current_return and shadow_return are required (NOT NULL,
    no DEFAULT) — caller must supply real values.
    """
    # Invalidate all cache entries for this symphony (keys now include db_file as 3rd element).
    stale = [k for k in _shadow_cr_cache if k[0] == symphony_id]
    for k in stale:
        del _shadow_cr_cache[k]
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.execute(
            "INSERT INTO shadow_history "
            "(ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
            " current_return, shadow_return, is_post_trigger, trigger_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_utc,
                ts_et,
                trading_day,
                symphony_id,
                account_id,
                cycle_id,
                current_return,
                shadow_return,
                is_post_trigger,
                trigger_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.error("record_shadow_observation failed for %s: %s", symphony_id, exc)


def prune_old_shadow_history(retention_days: int) -> int:
    """Delete shadow_history rows older than retention_days. Returns total rows deleted.

    Uses a portable subquery DELETE loop (PA-M1F-5) — works on all SQLite builds
    regardless of SQLITE_ENABLE_UPDATE_DELETE_LIMIT compile flag.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    total_deleted = 0
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        while True:
            cursor = conn.execute(
                "DELETE FROM shadow_history WHERE id IN "
                "(SELECT id FROM shadow_history WHERE ts_utc < ? ORDER BY ts_utc LIMIT 1000)",
                (cutoff,),
            )
            conn.commit()
            batch = cursor.rowcount
            total_deleted += batch
            if batch == 0:
                break
        conn.close()
    except Exception as exc:
        logging.error("prune_old_shadow_history failed: %s", exc)
    return total_deleted


def load_latest_shadow_row(symphony_id: str, trading_day: str) -> "dict | None":
    """Return the most-recent shadow_history row for a symphony+day, or None."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM shadow_history "
            "WHERE symphony_id = ? AND trading_day = ? "
            "ORDER BY ts_utc DESC LIMIT 1",
            (symphony_id, trading_day),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return dict(row)
    except Exception as exc:
        logging.error("load_latest_shadow_row failed for %s %s: %s", symphony_id, trading_day, exc)
        return None


def resume_shadow_baselines(bot_state: dict, trading_day: str) -> None:
    """Reconcile in-memory shadow_hwm cache against shadow_history on daemon restart.

    Reads the latest shadow_history row per symphony for trading_day and updates
    bot_state accordingly. The table is canonical; it wins on any divergence.
    AC-M1F.2.4, AC-M1F.7.2: must be called AFTER wipe_transient_state for the day.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symphony_id, current_return, shadow_return, is_post_trigger, trigger_id "
            "FROM shadow_history "
            "WHERE trading_day = ? "
            "GROUP BY symphony_id "
            "HAVING ts_utc = MAX(ts_utc)",
            (trading_day,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logging.error("resume_shadow_baselines failed: %s", exc)
        return

    for row in rows:
        s_id = row["symphony_id"]
        if s_id not in bot_state or not isinstance(bot_state[s_id], dict):
            continue
        # Update shadow_hwm from table's max(current_return) for the day
        bot_state[s_id]["shadow_hwm"] = row["current_return"]
        if row["is_post_trigger"]:
            bot_state[s_id]["triggered_at_return"] = row["shadow_return"]


def get_eod_shadow_row(symphony_id: str, trading_day: str) -> "dict | None":
    """Return the last shadow_history row for (symphony_id, trading_day) by ts_utc DESC.

    PA-M1F-5b: EOD divergence uses this row; it may not be the market-close row
    if the engine was down for the session's final interval.
    """
    return load_latest_shadow_row(symphony_id, trading_day)


def compute_shadow_hwm(symphony_id: str, trading_day: str) -> "float | None":
    """Return shadow_hwm = MAX(current_return) for (symphony_id, trading_day).

    BC-3: formula is max(current_return), NOT max(shadow_return). The shadow_hwm
    represents the counterfactual peak Composer-reported value during the day.
    Returns None when no rows exist for the given symphony+day.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        row = conn.execute(
            "SELECT MAX(current_return) FROM shadow_history "
            "WHERE symphony_id = ? AND trading_day = ?",
            (symphony_id, trading_day),
        ).fetchone()
        conn.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        logging.error("compute_shadow_hwm failed for %s %s: %s", symphony_id, trading_day, exc)
        return None


def get_shadow_divergence(trading_day: str) -> dict:
    """Return per-symphony and portfolio-level shadow divergence for the current day.

    Used by /api/state to populate the shadow_divergence key (PA-M1F-14).
    One lightweight GROUP BY query — not on the execution path.
    Returns {"by_symphony": {<id>: {"today": float|None, "cumulative": float|None}}, "portfolio_today": float|None}.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symphony_id, "
            "       (SELECT current_return - shadow_return "
            "        FROM shadow_history s2 "
            "        WHERE s2.symphony_id = s1.symphony_id AND s2.trading_day = ? "
            "          AND s2.is_post_trigger = 1 "
            "        ORDER BY s2.ts_utc DESC LIMIT 1) AS today_divergence "
            "FROM shadow_history s1 "
            "WHERE trading_day = ? "
            "GROUP BY symphony_id",
            (trading_day, trading_day),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logging.error("get_shadow_divergence failed: %s", exc)
        return {"by_symphony": {}, "portfolio_today": None}

    by_symphony: dict = {}
    divergences = []
    for row in rows:
        s_id = row["symphony_id"]
        div = row["today_divergence"]
        by_symphony[s_id] = {"today": div, "cumulative": None}
        if div is not None:
            divergences.append(div)

    portfolio_today = (sum(divergences) / len(divergences)) if divergences else None
    return {"by_symphony": by_symphony, "portfolio_today": portfolio_today}


def get_triggers(
    *,
    since: str | None = None,
    symphony_id: str | None = None,
    reason: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Read exit_triggers rows with optional filters.

    account_id is excluded from the returned dicts (PA-9).
    limit is server-side clamped to 500 max.
    """
    limit = min(limit, 500)
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("ts_utc > ?")
        params.append(since)
    if symphony_id:
        clauses.append("symphony_id = ?")
        params.append(symphony_id)
    if reason:
        clauses.append("triggered_reason = ?")
        params.append(reason)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT id, ts_utc, ts_et, symphony_id, triggered_reason, at_return, gate_state_json, cycle_id "
        f"FROM exit_triggers {where} ORDER BY ts_utc DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def prune_old_triggers(retention_days: int) -> int:
    """Delete exit_triggers rows older than retention_days, in batches of 1000.

    Returns the total number of rows deleted.
    Called by the daily scheduled task in app.py — never during a cycle write.
    Batched DELETE with LIMIT avoids long write locks on the shared state DB.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _PRUNE_BATCH_SIZE = 1000
    deleted_total = 0
    try:
        conn = get_connection()
        while True:
            cursor = conn.execute(
                "DELETE FROM exit_triggers WHERE id IN "
                "(SELECT id FROM exit_triggers WHERE ts_utc < ? LIMIT ?)",
                (cutoff, _PRUNE_BATCH_SIZE),
            )
            conn.commit()
            deleted_total += cursor.rowcount
            if cursor.rowcount == 0:
                break
        conn.close()
    except Exception as exc:
        logging.error("prune_old_triggers failed: %s", exc)
    return deleted_total


# Initialize tables on import
init_db()
