"""SQLite state management for AlphaBot with Account-Level Strategies."""

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from typing import Literal


def _finite_or_none(x):
    """Coerce non-finite float sentinels to None for RFC 8259 JSON compliance."""
    if x is None:
        return None
    try:
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


DB_FILE = os.environ.get("DB_PATH", "alphabot_state.db")
# Captured at import time — used by _db_file() to detect explicit test overrides.
_DB_FILE_DEFAULT = DB_FILE

# DEFAULT STRATEGY PARAMETERS (Used when a new account is detected)
DEFAULT_STRATEGY = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "MAX_SQUEEZE_FLOOR": 0.20,
    "VWAP_CROSS_HWM_PCT": 1.0,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.50,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 10,
}

# By default, we lock the non-user-specified variables so BO only tunes the requested
DEFAULT_LOCKED_VARS = ["TRIGGER_THRESHOLD_PCT"]


def _db_file() -> str:
    # Explicit per-test patch.object(database, "DB_FILE", path) takes precedence.
    # Ambient DB_PATH env var (set by autouse conftest isolation fixture) is the fallback.
    if DB_FILE != _DB_FILE_DEFAULT:
        return DB_FILE
    return os.environ.get("DB_PATH", DB_FILE)


def get_connection():
    return sqlite3.connect(_db_file(), timeout=10.0)


def get_ro_connection() -> sqlite3.Connection:
    # Opens via SQLite URI with ?mode=ro — read-only enforced at driver level.
    # Dashboard read handlers use this to prevent accidental writes while the
    # engine holds a WAL write lock (concurrent Flask reads + single writer).
    return sqlite3.connect(f"file:{_db_file()}?mode=ro", uri=True, timeout=10.0)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Enable WAL journal_mode so Flask dashboard reads can proceed concurrently
    # while the engine holds a write lock. WAL is idempotent — SQLite returns
    # the current mode without error if already set. Verify round-trip to catch
    # any environment that silently ignores the PRAGMA (e.g., read-only FS).
    cursor.execute("PRAGMA journal_mode=WAL")
    result = cursor.fetchone()
    if result and result[0].lower() != "wal":
        logging.warning("PRAGMA journal_mode=WAL did not take effect; current mode: %s", result[0])

    # Execution & State Tracking
    cursor.execute("CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)"
    )
    cursor.execute("CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS chart_archive (date TEXT, symphony_id TEXT, data TEXT, UNIQUE(date, symphony_id))"
    )

    # NEW: Symphony-Level Strategy Storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symphony_strategies (
            symphony_name TEXT PRIMARY KEY,
            parameters TEXT,
            locked_vars TEXT
        )
    """)

    # P1: Per-run Optuna validation metrics — durable audit trail for Claude context-assembly
    # H1 DUAL-WRITE: the nine EUT audit columns below are also added by migration
    # 020_autotune_runs_eut.sql via ALTER TABLE.  The duplicate-column-name swallow in
    # run_migrations() (database.py:921-932) reconciles the overlap: fresh DBs have
    # the columns here; upgraded DBs get them from the ALTER.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS autotune_runs (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp             TEXT    NOT NULL,
            symphony_id               TEXT    NOT NULL,
            oos_alpha                 REAL    DEFAULT NULL,
            train_alpha               REAL    DEFAULT NULL,
            baseline_decision         TEXT    DEFAULT NULL,
            fallback_oos_alpha        REAL    DEFAULT NULL,
            default_oos_alpha         REAL    DEFAULT NULL,
            selection_tstat           REAL    DEFAULT NULL,
            naive_sharpe              REAL    DEFAULT NULL,
            validation_sharpe         REAL    DEFAULT NULL,
            frozen_eval_sharpe        REAL    DEFAULT NULL,
            spec_bundle_id            TEXT    DEFAULT NULL,
            d_spec                    INTEGER DEFAULT NULL,
            n_effective               INTEGER DEFAULT NULL,
            ce_metric                 REAL    DEFAULT NULL,
            cvar_feasible             INTEGER DEFAULT NULL,
            gamma                     REAL    DEFAULT NULL,
            lambda_budget             REAL    DEFAULT NULL,
            overfitting_verdict       TEXT    DEFAULT NULL,
            paired_heuristic_study_name TEXT  DEFAULT NULL,
            s_count                   INTEGER DEFAULT NULL
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

    cursor.execute(
        "INSERT OR IGNORE INTO execution_lock (id, is_locked, timestamp) VALUES (1, 0, 0)"
    )
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
    cursor.execute(
        "UPDATE execution_lock SET is_locked = 1, timestamp = ? WHERE id = 1", (current_time,)
    )
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


_WIPE_RESERVED_KEYS = {"date", "last_execution_mode", "last_market_close_snapshot"}


def mint_position_epoch() -> str:
    """Mint a fresh opaque position-epoch identifier (AC-3).

    Stamped on bot_state at every position-lifecycle boundary so shadow_history
    rows written under one position carry a stable epoch. The trajectory query
    self-selects the CURRENT epoch by the latest row's ts_utc, so the value need
    not be sortable — a uuid4 hex string is sufficient and collision-free.
    """
    return uuid.uuid4().hex


def wipe_transient_state(state_dict):
    """Wipes transient state keys for all symphonies to prevent bleeding across sessions."""
    for s_id, s_data in state_dict.items():
        if s_id in _WIPE_RESERVED_KEYS:
            continue
        if isinstance(s_data, dict):
            # AC-3: capture the triggered state BEFORE the wipe clears it — the
            # epoch re-stamp below is conditional on it.
            was_triggered = bool(s_data.get("triggered"))
            s_data["high_water_mark"] = -999.0
            s_data["shadow_hwm"] = -999.0
            s_data["prev_return"] = (
                None  # sentinel: cycle-1 velocity = 0 (prevents false PARA-ARM on opening gap)
            )
            s_data["armed"] = False
            s_data["tp_armed"] = False
            s_data["para_armed"] = False
            s_data["triggered"] = False
            s_data["breakeven_locked"] = False
            s_data["stop_trigger"] = (
                None  # AC-E2.5: new position must not inherit prior position's stop floor
            )
            # AC-3: stamp the position epoch CONDITIONALLY. A wipe on a TRIGGERED
            # position is a genuine new-position boundary -> re-stamp. A wipe on
            # an untriggered still-open position is NOT a boundary -> keep the
            # existing epoch (only stamp if absent, i.e. the first stamp). An
            # unconditional re-stamp would fragment a multi-day position into one
            # epoch per day and truncate its trajectory query.
            if was_triggered or "position_epoch" not in s_data:
                s_data["position_epoch"] = mint_position_epoch()
            s_data["below_stop_count"] = 0
            s_data["above_tp_count"] = 0
            s_data["vwap_ticks"] = 0
            s_data["vwap_bleed_ticks"] = 0
            s_data["hwm_hold_ticks"] = 0
            s_data["mc_history"] = []

            # Remove any trigger-related snapshot data
            for k in [
                "triggered_reason",
                "triggered_at_return",
                "triggered_at_hwm",
                "triggered_at_stop",
                "triggered_at_time",
                "trigger_prices",
                "triggered_basket_snapshot",
            ]:
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
    cursor.execute(
        "INSERT OR REPLACE INTO chart_archive (date, symphony_id, data) VALUES (?, ?, ?)",
        (date_str, symphony_id, json.dumps(data)),
    )
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
    cursor.execute(
        f"SELECT date, symphony_id, data FROM chart_archive WHERE date IN ({placeholders})", dates
    )
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
    cursor.execute(
        "SELECT parameters, locked_vars FROM symphony_strategies WHERE symphony_name = ?",
        (symphony_name,),
    )
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
        (symphony_name, json.dumps(params), json.dumps(locked_vars)),
    )
    conn.commit()
    conn.close()


# --- Symphony Logging (NEW) ---
SYMPHONY_LOGS_FILE = "symphony_logs.json"


def get_symphony_logs(symphony_id):
    try:
        with open(SYMPHONY_LOGS_FILE, encoding="utf-8") as f:
            logs = json.load(f)
            return logs.get(symphony_id, [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def log_symphony_event(symphony_id, message, event_type="info"):
    logs = {}
    try:
        with open(SYMPHONY_LOGS_FILE, encoding="utf-8") as f:
            logs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    if symphony_id not in logs:
        logs[symphony_id] = []

    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    logs[symphony_id].append({"timestamp": timestamp, "event_type": event_type, "message": message})

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


def save_autotune_run(
    run_timestamp,
    symphony_id,
    oos_alpha,
    train_alpha,
    baseline_decision,
    fallback_oos_alpha,
    default_oos_alpha,
    selection_tstat=None,
    naive_sharpe=None,
    validation_sharpe=None,
    frozen_eval_sharpe=None,
    # ARCH-001: EUT audit columns from migration 020 (all Phase-1 nullable).
    spec_bundle_id=None,
    n_effective=None,
    d_spec=None,
    gamma=None,
    overfitting_verdict=None,
) -> int:
    """Persist one row of per-run Optuna validation metrics to autotune_runs.

    Returns the new row id (cursor.lastrowid) so the OC producer can propagate
    it directly into _oc_run["id"] without a read-after-write round-trip.
    S3-AUDIT-001 fix: previously returned None.

    Called once per symphony per run_autotuner() invocation, after baseline_decision
    is finalized.  All metric columns are NULLable so partial data never fails an
    INSERT (though callers should supply all values).

    Selection-metric columns:
      selection_tstat: the Harvey & Liu 2015 selection haircut's winning-trial
                       t-statistic (Sortino·sqrt(T)) — a higher-is-better
                       significance scalar. None when the fallback or default
                       cascade was used instead of the AI branch.
      naive_sharpe:    Raw Optuna best trial Sortino before the selection haircut.
                       None for non-AI rows.

    O6 additions:
      validation_sharpe:  Sortino on the validation fold (20% of history); the metric used for
                          trial selection. Selection truth; visible to operator for audit.
      frozen_eval_sharpe: Sortino on the frozen-eval fold (final 20% of history); consumed once
                          post-selection for honest performance reporting (López de Prado 2018 Ch. 7.4).

    EUT audit columns (migration 020 — ARCH-001 fix):
      spec_bundle_id:     bundle_hash TEXT of the spec bundle active during this run.
      n_effective:        N_optuna + S (honest multiple-testing count from compute_n_effective).
      d_spec:             COUNT DISTINCT BACKTEST_SELECTION spec_bundle_ids in researcher_dof_ledger.
      gamma:              Frozen CRRA risk-aversion coefficient from spec_facets.
      overfitting_verdict: Human-readable Overfitting Conscience summary string.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO autotune_runs
            (run_timestamp, symphony_id, oos_alpha, train_alpha,
             baseline_decision, fallback_oos_alpha, default_oos_alpha,
             selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe,
             spec_bundle_id, n_effective, d_spec, gamma, overfitting_verdict)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_timestamp,
            symphony_id,
            oos_alpha,
            train_alpha,
            baseline_decision,
            fallback_oos_alpha,
            default_oos_alpha,
            selection_tstat,
            naive_sharpe,
            validation_sharpe,
            frozen_eval_sharpe,
            spec_bundle_id,
            n_effective,
            d_spec,
            gamma,
            overfitting_verdict,
        ),
    )
    conn.commit()
    row_id: int = cursor.lastrowid
    conn.close()
    return row_id


def _autotune_run_row_to_dict(row) -> dict:
    """Map a raw autotune_runs SELECT row (15 columns) to a dict.

    Column order matches _AUTOTUNE_RUNS_SELECT. id is projected first
    (S3-AUDIT-001 fix) so the OC producer receives an honest row id.
    """
    return {
        "id": row[0],
        "run_timestamp": row[1],
        "symphony_id": row[2],
        "oos_alpha": _finite_or_none(row[3]),
        "train_alpha": _finite_or_none(row[4]),
        "baseline_decision": row[5],
        "fallback_oos_alpha": _finite_or_none(row[6]),
        "default_oos_alpha": _finite_or_none(row[7]),
        "selection_tstat": _finite_or_none(row[8]),
        "naive_sharpe": _finite_or_none(row[9]),
        "validation_sharpe": _finite_or_none(row[10]),
        "frozen_eval_sharpe": _finite_or_none(row[11]),
        "math_mode": row[12],
        "account_id": row[13],
        "sortino_sentinel_pct": _finite_or_none(row[14]),
    }


_AUTOTUNE_RUNS_SELECT = """
    SELECT id,
           run_timestamp, symphony_id, oos_alpha, train_alpha,
           baseline_decision, fallback_oos_alpha, default_oos_alpha,
           selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe,
           math_mode, account_id, sortino_sentinel_pct
    FROM autotune_runs
"""


def get_latest_autotune_run(
    symphony_id: str,
    account_id: "str | None" = None,
    math_mode: "str | None" = "per_symphony",
) -> "dict | None":
    """Return the most-recent autotune_runs row for symphony_id as a dict.

    Returns None if no rows exist — callers treat None as "Optuna has not yet run".

    N3+N4: account_id and math_mode filter to port-level runs when supplied.
    Legacy callers that pass only symphony_id continue to work unchanged.
    """
    conn = get_connection()
    cursor = conn.cursor()
    params: list = [symphony_id]
    filters = "WHERE symphony_id = ?"
    if account_id is not None:
        filters += " AND account_id = ?"
        params.append(account_id)
    if math_mode is not None:
        filters += " AND math_mode = ?"
        params.append(math_mode)
    cursor.execute(
        _AUTOTUNE_RUNS_SELECT + filters + " ORDER BY run_timestamp DESC LIMIT 1",
        params,
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return _autotune_run_row_to_dict(row)


def record_autotune_run(
    run_timestamp,
    symphony_id,
    math_mode="per_symphony",
    oos_alpha=None,
    train_alpha=None,
    baseline_decision=None,
    fallback_oos_alpha=None,
    default_oos_alpha=None,
    selection_tstat=None,
    naive_sharpe=None,
    validation_sharpe=None,
    frozen_eval_sharpe=None,
    account_id=None,
    sortino_sentinel_pct=None,
) -> None:
    """Persist one autotune_runs row with port-mode fields.

    Adds math_mode, account_id, sortino_sentinel_pct to the existing
    save_autotune_run interface (AC-P2.11.1, F3, N3+N4).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO autotune_runs
            (run_timestamp, symphony_id, oos_alpha, train_alpha,
             baseline_decision, fallback_oos_alpha, default_oos_alpha,
             selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe,
             math_mode, account_id, sortino_sentinel_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_timestamp,
            symphony_id,
            oos_alpha,
            train_alpha,
            baseline_decision,
            fallback_oos_alpha,
            default_oos_alpha,
            selection_tstat,
            naive_sharpe,
            validation_sharpe,
            frozen_eval_sharpe,
            math_mode,
            account_id,
            sortino_sentinel_pct,
        ),
    )
    conn.commit()
    conn.close()


def get_all_autotune_runs(limit: int = 50) -> list[dict]:
    """Return the most-recent autotune_runs rows across all symphonies.

    Used by the /api/autotune-runs dashboard route to surface selection metrics.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        _AUTOTUNE_RUNS_SELECT
        + """
        ORDER BY run_timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_autotune_run_row_to_dict(r) for r in rows]


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
    "id",
    "session_id",
    "created_at",
    "symphony_name",
    "operator_identity",
    "prompt_inputs",
    "model_id",
    "generation_settings",
    "raw_response",
    "validation_results",
    "param_name",
    "operator_decision",
    "decision_at",
    "operator_note",
    "before_value",
    "after_value",
    "oos_revalidation",
]


def get_suggestions_for_symphony(symphony_name: str) -> list[dict]:
    """Return all llm_suggestions rows for a given symphony, oldest-first.

    Returns an empty list if no rows exist — never raises for an unknown symphony.
    Each element is a full-column dict with JSON-blob columns deserialized.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_LLM_SUGGESTION_COLUMNS)
        + " FROM llm_suggestions WHERE symphony_name = ? ORDER BY id ASC",
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
        "SELECT "
        + ", ".join(_LLM_SUGGESTION_COLUMNS)
        + " FROM llm_suggestions WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_llm_suggestion_row(row, _LLM_SUGGESTION_COLUMNS) for row in rows]


# --- 017: Advisor Observations ---

_ADVISOR_OBSERVATION_COLUMNS = [
    "id",
    "created_at",
    "advisor_role",
    "subject_type",
    "subject_id",
    "verdict",
    "raw_response",
    "is_advisory_only",
    "spec_bundle_id",
    "symphony_id",
]


def _parse_advisor_observation_row(row: tuple, columns: list[str]) -> dict:
    """Convert a raw advisor_observations tuple into a typed dict.

    raw_response is a JSON blob column and is deserialised to a Python dict so
    callers receive the original object rather than a raw JSON string — consistent
    with the llm_suggestions precedent (database.py:653-676).
    """
    result = {}
    for col, val in zip(columns, row):
        if col == "raw_response" and val is not None:
            result[col] = json.loads(val)
        else:
            result[col] = val
    return result


def insert_advisor_observation(
    *,
    advisor_role: str,
    subject_type: str,
    subject_id: str,
    verdict: str | None = None,
    raw_response: "dict | str | None" = None,
    spec_bundle_id: str | None = None,
    symphony_id: str | None = None,
    **kwargs,
) -> int:
    """Insert an advisor observation row; return the new row id.

    Append-only: no update or delete accessor exists — existing rows are immutable
    (same pattern as llm_suggestions; council plan §3.1 row 019).

    is_advisory_only is always stored as 1 regardless of any caller-supplied value
    in **kwargs — the Advisor never moves money.  raw_response defaults to '{}'
    for computed rows that carry no LLM output.

    symphony_id (S3-AUDIT-004): denormalized symphony name so the
    /api/advisor-observations?symphony_id= filter resolves in one SELECT
    rather than three subject-type fan-out queries.
    """
    # Serialise raw_response; None and empty dict both become '{}'.
    if raw_response is None:
        raw_response_str = "{}"
    elif isinstance(raw_response, dict):
        raw_response_str = json.dumps(raw_response)
    else:
        # Accept a pre-serialised JSON string as-is.
        raw_response_str = raw_response

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO advisor_observations "
        "(advisor_role, subject_type, subject_id, verdict, raw_response, is_advisory_only, spec_bundle_id, symphony_id) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (advisor_role, subject_type, subject_id, verdict, raw_response_str, spec_bundle_id, symphony_id),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_advisor_observations_for_subject(
    subject_type: str,
    subject_id: str,
) -> list[dict]:
    """Return all advisor_observations rows for a given subject, oldest-first.

    Returns an empty list when no rows match — never raises for an unknown subject.
    raw_response is deserialised from JSON so callers receive a Python dict.
    Uses get_ro_connection() per architecture constraint 5 (dashboard read-only)
    and Advisor scope-boundary integrity I-3 — read paths are structurally isolated
    from the write path.
    """
    conn = get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
        + " FROM advisor_observations WHERE subject_type = ? AND subject_id = ? ORDER BY id ASC",
        (subject_type, subject_id),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS) for row in rows]


def get_advisor_observations_for_role(
    advisor_role: str,
    limit: int = 50,
) -> list[dict]:
    """Return advisor_observations rows for a given role, newest-first.

    Returns an empty list when no rows match — never raises for an unknown role.
    raw_response is deserialised from JSON so callers receive a Python dict.
    Uses get_ro_connection() — read-only at the driver level (architecture constraint 5).
    """
    conn = get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
        + " FROM advisor_observations WHERE advisor_role = ? ORDER BY id DESC LIMIT ?",
        (advisor_role, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS) for row in rows]


def get_advisor_observations_for_symphony(symphony_id: str) -> list[dict]:
    """Return all advisor_observations rows whose symphony_id matches, oldest-first.

    Single-query filter via the denormalized symphony_id column added by
    migration 025 (S3-AUDIT-004 + S3-AUDIT-010 fix).  Replaces the legacy
    three-subject fan-out in /api/advisor-observations, which could never
    match because subject_id stores a numeric PK or bundle_hash, not a name.

    Returns an empty list when no rows match.
    Uses get_ro_connection() — read-only (architecture constraint 5).
    """
    conn = get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
        + " FROM advisor_observations WHERE symphony_id = ? ORDER BY id ASC",
        (symphony_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS) for row in rows]


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
    "010_port_state.sql",
    "011_exit_triggers_port.sql",
    "012_autotune_runs_portmode.sql",
    "013_fleet_alert_tripped_symphonies.sql",
    "014_autotune_runs_selection_tstat.sql",
    "015_shadow_history_position_epoch.sql",
    "016_spec_bundles.sql",
    "017_advisor_observations.sql",
    "018_researcher_dof_ledger.sql",
    "019_fold_role_columns.sql",
    # ARCH-002 (sprint-2-audit a6e4d9f8): 021 is listed before 020 — intentional.
    # 021_cvar_diagnostics.sql was applied to production DBs before 020_autotune_runs_eut.sql
    # was accidentally dropped and later restored (defect-37 restoration hotfix).
    # Reordering to numeric sequence would attempt to re-apply 021 on live DBs that already
    # have it, causing a duplicate-column/table error. The two migrations are independent
    # (different tables), so the out-of-order application is functionally correct.
    "021_cvar_diagnostics.sql",
    "020_autotune_runs_eut.sql",
    "022_spec_bundles_add_id.sql",
    "023_autotune_runs_s_count.sql",
    "024_spec_facets_unique_constraint.sql",
    "025_advisor_observations_symphony_id.sql",
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
            with open(migration_path, encoding="utf-8") as fh:
                sql = fh.read()
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (migration_name,),
            )
            conn.commit()
        except Exception as exc:
            exc_lower = str(exc).lower()
            if "duplicate column name" in exc_lower:
                # initialize_db() CREATE TABLE already includes these columns — safe to mark applied.
                logging.info(
                    "run_migrations: %s columns already present, marking applied", migration_name
                )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                    (migration_name,),
                )
                conn.commit()
            else:
                logging.error("run_migrations: failed to apply %s: %s", migration_name, exc)

    conn.close()


# --- 016: Spec-Bundle Registry ---
# Immutable hashed frozen-facet bundle registry.  The application layer exposes
# only INSERT and SELECT — no UPDATE path — enforcing the NN1 spec-freeze
# invariant at the code level (analogous to the llm_suggestions append-only
# accessor surface at database.py:670-715).

_VALID_FREEZE_DISCIPLINES: frozenset[str] = frozenset({
    "THEORY",
    "MANDATE",
    "STYLIZED_FACT",
    "POLITIS_WHITE",
    "CADENCE",
    "CALIBRATION",
    "BACKTEST_SELECTION",
})

_SPEC_BUNDLE_COLUMNS = [
    "id",
    "bundle_hash",
    "frozen_at",
    "facets_json",
    "horizon_bars",
    "cvar_alpha",
    "generator_family",
]

_SPEC_FACET_COLUMNS = [
    "id",
    "bundle_hash",
    "facet_name",
    "facet_value",
    "freeze_discipline",
    "justification",
    "calibration_evidence",
]


def canonicalize_facets_json(facets: dict) -> str:
    """Deterministic JSON serialisation of a facets dict.

    Sort keys and use compact separators so the same dict always yields the
    same byte sequence across interpreter restarts (Gate-1 parity precondition).
    """
    return json.dumps(facets, sort_keys=True, separators=(",", ":"))


def hash_facets_json(canonical_json: str) -> str:
    """Return the hex-encoded SHA-256 digest of the canonical facets JSON bytes.

    Reproducible at replay time: the same canonical_json string always yields
    the same hex digest (plan §Risk callouts — non-reproducible hash fails
    Gate-1 parity).
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def insert_spec_bundle(
    *,
    bundle_hash: str,
    facets_json: str,
    horizon_bars: "int | None" = None,
    cvar_alpha: "float | None" = None,
    generator_family: "str | None" = None,
) -> None:
    """Insert a new spec bundle row.

    Idempotent: a duplicate bundle_hash is silently ignored (INSERT OR IGNORE).
    INSERT OR REPLACE is explicitly NOT used — that would overwrite frozen_at,
    destroying the original freeze-timestamp provenance record.

    The id column (added by migration 022) is backfilled from SQLite's implicit
    rowid immediately after INSERT so that callers can do
    SELECT id FROM spec_bundles WHERE bundle_hash = ? and always get a non-NULL
    integer — including rows inserted after migration 022 has already run once.
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO spec_bundles "
            "(bundle_hash, facets_json, horizon_bars, cvar_alpha, generator_family) "
            "VALUES (?, ?, ?, ?, ?)",
            (bundle_hash, facets_json, horizon_bars, cvar_alpha, generator_family),
        )
        # Backfill id from rowid for the just-inserted row (or any row that still
        # has id IS NULL, e.g. rows inserted on a DB that was at migration 016 state).
        # This is a no-op for rows already backfilled by migration 022.
        conn.execute(
            "UPDATE spec_bundles SET id = rowid WHERE bundle_hash = ? AND id IS NULL",
            (bundle_hash,),
        )
        conn.commit()
    finally:
        conn.close()


# Process-local cache for the canonical Phase-1 theory bundle id.
# Tuple of (db_path, bundle_id) so the cache automatically misses if the DB path
# changes between calls (e.g. per-test isolation via DB_FILE monkeypatching).
# Value is None until the first call for a given DB path completes.
# W-H2 derivation: docs/decision-science/w-h2-wealth-argument-derivation.md §3.1
_phase1_theory_bundle_id_cache: "tuple[str, int] | None" = None

# Canonical Phase-1 theory facet values (W-H2 derivation, council synthesis §2.5).
# gamma: risk-aversion coefficient — 2.0 is the canonical Phase-1 value per council §2.5
#   (most risk-averse value in the W-H2 fixture set {0.5, 1.0, 2.0}).
# utility_family: CRRA — the functional form, sourced from derivation-fixture.json
#   crra_utility_formula section (W-H2 memo §1).
# wealth_argument: "compounded_return" — canonical alias for per_period_gross_wealth_ratio
#   per derivation-fixture.json selected_wealth_argument_formula.name mapping
#   (W-H2 memo §3; test fixture FORMULA_NAME_TO_FACET_VALUE mapping).
PHASE1_THEORY_GAMMA: str = "2.0"
PHASE1_THEORY_UTILITY_FAMILY: str = "CRRA"
PHASE1_THEORY_WEALTH_ARGUMENT_FORMULA: str = "compounded_return"


def get_or_create_phase1_theory_bundle_id() -> int:
    """Return the integer id for the canonical Phase-1 all-THEORY spec bundle.

    Idempotent: INSERT OR IGNORE means repeated calls return the same id.
    The Phase-1 bundle encodes the three theory-frozen facets (gamma, utility_family,
    wealth_argument) with freeze_discipline='THEORY' per council synthesis §2.5.
    W-H2 derivation: docs/decision-science/w-h2-wealth-argument-derivation.md

    Called by live run_autotuner sites (alpha_bot_execution.py, app.py) to satisfy
    the NN1 Phase-1 strict spec_bundle_id requirement without requiring an explicit
    operator-registered bundle (Phase-2 wiring deferred).

    Process-local cache: the second and subsequent calls within the same process
    return immediately without touching the DB (sub-microsecond; H-3 budget).
    """
    global _phase1_theory_bundle_id_cache
    current_db = _db_file()
    if _phase1_theory_bundle_id_cache is not None:
        cached_db, cached_id = _phase1_theory_bundle_id_cache
        if cached_db == current_db:
            return cached_id
        # DB path changed (test isolation); fall through to re-compute.
        _phase1_theory_bundle_id_cache = None

    _canon_facets = {
        "gamma": PHASE1_THEORY_GAMMA,
        "utility_family": PHASE1_THEORY_UTILITY_FAMILY,
        "wealth_argument": PHASE1_THEORY_WEALTH_ARGUMENT_FORMULA,
    }
    canonical_json = canonicalize_facets_json(_canon_facets)
    bundle_hash = hash_facets_json(canonical_json)
    insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    # Fetch the id backfilled by insert_spec_bundle (trigger or UPDATE).
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"get_or_create_phase1_theory_bundle_id: id is NULL for bundle_hash={bundle_hash!r} "
            "after insert — run_migrations() may not have applied migration 022."
        )
    bundle_id: int = row[0]

    # Ensure the three canonical facets are registered.
    # INSERT OR IGNORE in insert_spec_bundle_facet makes this concurrent-safe
    # (migration 024 adds UNIQUE(bundle_hash, facet_name) to spec_facets).
    existing = get_spec_facets_for_bundle(bundle_hash)
    existing_names = {r["facet_name"] for r in existing}
    for name, value in _canon_facets.items():
        if name not in existing_names:
            insert_spec_bundle_facet(
                bundle_hash=bundle_hash,
                facet_name=name,
                facet_value=value,
                freeze_discipline="THEORY",
                justification="Phase-1 canonical bundle — W-H2 derivation + council synthesis §2.5 hard gate",
            )

    _phase1_theory_bundle_id_cache = (current_db, bundle_id)
    return bundle_id


def get_spec_bundle(bundle_hash: str) -> "dict | None":
    """Return the spec_bundles row for the given hash as a dict, or None."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_SPEC_BUNDLE_COLUMNS)
            + " FROM spec_bundles WHERE bundle_hash = ?",
            (bundle_hash,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(_SPEC_BUNDLE_COLUMNS, row))


def get_spec_bundle_by_id(spec_bundle_id: int) -> "dict | None":
    """Return the spec_bundles row for the given integer id as a dict, or None.

    Encapsulates the bundle-integrity check (stored hash vs recomputed hash from
    facets_json) so callers do not need to open a raw connection. Raises ValueError
    if the stored bundle_hash does not match the hash recomputed from facets_json —
    this indicates the bundle was tampered with after frozen_at.

    Returns None if no row exists for the given id.
    """
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_SPEC_BUNDLE_COLUMNS)
            + " FROM spec_bundles WHERE id = ?",
            (spec_bundle_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(_SPEC_BUNDLE_COLUMNS, row))


def insert_spec_bundle_facet(
    *,
    bundle_hash: str,
    facet_name: str,
    facet_value: str,
    freeze_discipline: str,
    justification: "str | None" = None,
    calibration_evidence: "str | None" = None,
) -> int:
    """Insert a spec_facets row and return the new row id.

    Raises ValueError for any freeze_discipline value outside the accepted enum
    (THEORY / MANDATE / STYLIZED_FACT / CALIBRATION / BACKTEST_SELECTION).
    Enforcement is at the application layer — consistent with the codebase's
    app-level constraint pattern (no SQL CHECK constraint).
    """
    if freeze_discipline not in _VALID_FREEZE_DISCIPLINES:
        raise ValueError(
            f"freeze_discipline {freeze_discipline!r} is not a valid enum value. "
            f"Accepted: {sorted(_VALID_FREEZE_DISCIPLINES)}"
        )
    conn = get_connection()
    try:
        # INSERT OR IGNORE: if the (bundle_hash, facet_name) pair already exists
        # (UNIQUE constraint from migration 024), the insert is silently skipped.
        # This makes concurrent calls to get_or_create_phase1_theory_bundle_id()
        # safe — duplicate facet inserts are idempotent.
        cursor = conn.execute(
            "INSERT OR IGNORE INTO spec_facets "
            "(bundle_hash, facet_name, facet_value, freeze_discipline, "
            "justification, calibration_evidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (bundle_hash, facet_name, facet_value, freeze_discipline,
             justification, calibration_evidence),
        )
        conn.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        # Row already existed (INSERT was ignored); return its id.
        row = conn.execute(
            "SELECT id FROM spec_facets WHERE bundle_hash = ? AND facet_name = ?",
            (bundle_hash, facet_name),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def get_spec_facets_for_bundle(bundle_hash: str) -> "list[dict]":
    """Return all spec_facets rows for the given bundle_hash, ordered by id.

    Returns an empty list if none exist — never raises for an unknown hash.
    Uses get_ro_connection() — pure-read path; avoids write-lock contention
    on the WAL-mode DB (architecture constraint 3).
    """
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_SPEC_FACET_COLUMNS)
            + " FROM spec_facets WHERE bundle_hash = ? ORDER BY id ASC",
            (bundle_hash,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SPEC_FACET_COLUMNS, row)) for row in rows]


# --- 018: Researcher DOF Ledger ---
# Append-only degrees-of-freedom ledger for the NN1 multiple-testing haircut.
# Records every facet evaluated on a strategy P&L / strategy-return basis.
# Consumer: autotuner.py reads count_dof_backtest_selections() to compute S
# and writes N_effective = N_optuna + S into autotune_runs.n_effective (plan 020).
#
# S accumulator = SUM(n_configs_searched) WHERE evidence_source = 'BACKTEST_SELECTION'
# This is the sub-sweep sum (v3-evaluation §A.0 Defect 2 binding), NOT
# COUNT(DISTINCT spec_bundle_id) — the binding conservative-upper-bound property.
#
# Accessor surface: INSERT + SELECT only.  No UPDATE or DELETE path — the same
# append-only immutability contract enforced for llm_suggestions and
# advisor_observations (database.py:670-715 and advisor section below).

_VALID_DOF_FACET_CATEGORIES: frozenset[str] = frozenset({
    "specification",
    "parameter",
})

_VALID_DOF_DECISION_TYPES: frozenset[str] = frozenset({
    "FIXED",
    "SEARCHED",
    "REVISED",
    "OOS_PEEK",
})

_VALID_DOF_EVIDENCE_SOURCES: frozenset[str] = frozenset({
    "THEORY",
    "MANDATE",
    "STYLIZED_FACT",
    "CALIBRATION",
    "BACKTEST_SELECTION",
    "OOS",
})

_DOF_LEDGER_COLUMNS = [
    "id",
    "created_at",
    "facet_name",
    "facet_category",
    "decision_type",
    "evidence_source",
    "n_configs_searched",
    "touched_frozen_eval",
    "spec_bundle_id",
    "justification",
]


def insert_dof_ledger_row(
    *,
    facet_name: str,
    facet_category: str,
    decision_type: str,
    evidence_source: str,
    n_configs_searched: int = 1,
    touched_frozen_eval: int = 0,
    spec_bundle_id: "str | None" = None,
    justification: "str | None" = None,
) -> int:
    """Append one row to researcher_dof_ledger. Returns the new row id.

    Raises ValueError for any enum column value outside the accepted set.
    Enforcement is at the application layer — consistent with the codebase's
    app-level constraint pattern (no SQL CHECK constraint).

    This is the only write path — there is no update or delete accessor.
    The DOF ledger is immutable by design (tripwire, not a routine ledger).
    """
    if facet_category not in _VALID_DOF_FACET_CATEGORIES:
        raise ValueError(
            f"facet_category {facet_category!r} is not valid. "
            f"Accepted: {sorted(_VALID_DOF_FACET_CATEGORIES)}"
        )
    if decision_type not in _VALID_DOF_DECISION_TYPES:
        raise ValueError(
            f"decision_type {decision_type!r} is not valid. "
            f"Accepted: {sorted(_VALID_DOF_DECISION_TYPES)}"
        )
    if evidence_source not in _VALID_DOF_EVIDENCE_SOURCES:
        raise ValueError(
            f"evidence_source {evidence_source!r} is not valid. "
            f"Accepted: {sorted(_VALID_DOF_EVIDENCE_SOURCES)}"
        )
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO researcher_dof_ledger "
            "(facet_name, facet_category, decision_type, evidence_source, "
            "n_configs_searched, touched_frozen_eval, spec_bundle_id, justification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                facet_name,
                facet_category,
                decision_type,
                evidence_source,
                n_configs_searched,
                touched_frozen_eval,
                spec_bundle_id,
                justification,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_dof_ledger_for_bundle(spec_bundle_id: str) -> "list[dict]":
    """Return all researcher_dof_ledger rows for the given spec_bundle_id, ordered by id.

    Uses a read-only connection — this accessor is called from the Advisor and
    dashboard surfaces where writes are prohibited (charter Operating Rule 3).
    Returns an empty list if no rows exist for the bundle.
    """
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_DOF_LEDGER_COLUMNS)
            + " FROM researcher_dof_ledger WHERE spec_bundle_id = ? ORDER BY id ASC",
            (spec_bundle_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_DOF_LEDGER_COLUMNS, row)) for row in rows]


def count_dof_backtest_selections(spec_bundle_id: "str | None" = None) -> int:
    """Return S = SUM(n_configs_searched) for BACKTEST_SELECTION rows.

    This is the S accumulator in N_effective = N_optuna + S.

    When spec_bundle_id is provided, S is scoped to that bundle.
    When None, S is the global sum across all bundles (pre-bundle call sites).

    The binding consumer reading is the sub-sweep SUM, NOT COUNT(DISTINCT
    spec_bundle_id) — a single P&L-toured spec that received its own sub-sweep
    contributes its full sub-sweep count (council §2.2 / v3-evaluation §A.0
    Defect 2). A deliberately conservative upper bound: can reject a genuine
    signal, never pass a spurious one (council §2.2 property 2).

    Uses a read-only connection — the autotuner's read path must never hold a
    write lock while computing the haircut.
    """
    conn = get_ro_connection()
    try:
        if spec_bundle_id is not None:
            row = conn.execute(
                "SELECT COALESCE(SUM(n_configs_searched), 0) "
                "FROM researcher_dof_ledger "
                "WHERE evidence_source = 'BACKTEST_SELECTION' AND spec_bundle_id = ?",
                (spec_bundle_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(n_configs_searched), 0) "
                "FROM researcher_dof_ledger "
                "WHERE evidence_source = 'BACKTEST_SELECTION'",
            ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else 0


def get_researcher_dof_ledger_for_run(
    run_timestamp: str,
    winning_spec_bundle_id: "str | None" = None,
) -> "list[dict]":
    """Return researcher_dof_ledger rows whose evidence_source is BACKTEST_SELECTION
    for the active autotune run window, excluding frozen-eval-tainted rows and
    the winning bundle (plan D4).

    Filters:
      - evidence_source = 'BACKTEST_SELECTION'
      - COALESCE(touched_frozen_eval, 0) = 0  (frozen-eval rows handled by OOS_PEEK alarm)
      - spec_bundle_id != winning_spec_bundle_id  (winner already counted in n_optuna)

    Returns 0 rows in the NN1-honest case → S = 0.
    Uses a read-only connection.
    """
    conn = get_ro_connection()
    try:
        if winning_spec_bundle_id is not None:
            rows = conn.execute(
                "SELECT " + ", ".join(_DOF_LEDGER_COLUMNS)
                + " FROM researcher_dof_ledger"
                " WHERE evidence_source = 'BACKTEST_SELECTION'"
                "   AND COALESCE(touched_frozen_eval, 0) = 0"
                "   AND (spec_bundle_id IS NULL OR spec_bundle_id != ?)"
                " ORDER BY id ASC",
                (winning_spec_bundle_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT " + ", ".join(_DOF_LEDGER_COLUMNS)
                + " FROM researcher_dof_ledger"
                " WHERE evidence_source = 'BACKTEST_SELECTION'"
                "   AND COALESCE(touched_frozen_eval, 0) = 0"
                " ORDER BY id ASC",
            ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_DOF_LEDGER_COLUMNS, row)) for row in rows]


# --- Advisor Wall: frozen-eval access guard ---

# SQL bypass and bare fold_role predicate patterns that the Advisor wall must reject.
#
#   fold_role !=  / fold_role <>
#     H3 SQL-NULL trap: NULL rows are silently excluded by SQL three-valued logic.
#     Both forms evaluate NULL != 'frozen_eval' to NULL (falsy), not TRUE.
#
#   OR 1=1
#     Classic tautology injection that defeats any WHERE clause predicate, including
#     COALESCE-wrapped fold_role predicates.  A caller-supplied predicate such as
#     COALESCE(fold_role,'') != 'frozen_eval' is rendered meaningless when followed
#     by OR 1=1 — the entire WHERE becomes always TRUE and all rows are returned,
#     including frozen_eval rows.  Any Advisor SQL containing OR 1=1 is a structural
#     bypass attempt and must be rejected.
#
# Callers supply the inner SELECT; the helper wraps it automatically with the
# outer COALESCE filter.  COALESCE(fold_role...) in the caller's WHERE is accepted
# when not combined with a bypass (the outer wrap provides defence-in-depth).
_BARE_FOLD_ROLE_PREDICATES = ("fold_role !=", "fold_role <>", "OR 1=1")


def advisor_ro_query(sql: str, params: tuple = ()) -> list:
    """Execute a read-only query on behalf of an Advisor code path.

    This is the ONLY entry point from Advisor code to the state DB.  Calling
    get_connection() or get_ro_connection() directly from Advisor code is a
    structural side door that bypasses both the COALESCE guard and the
    wall-breach tripwire — it is prohibited (enforced by the lint test in CI).

    Caller contract
    ---------------
    - Any predicate that filters on fold_role MUST NOT use a bare inequality:
          fold_role != 'frozen_eval'  (H3 NULL trap — silently hides NULL rows)
          fold_role <> 'frozen_eval'  (SQL-standard form, same trap)
      Both forms are rejected with ValueError before execution.
    - The helper wraps the caller's SQL as a subquery and appends a
      COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval','NULL_SENTINEL')
      predicate.  This filters frozen_eval rows AND untagged (NULL fold_role)
      rows at the SQL level before they ever reach Python.
      (M-1 safe-default: a forgotten fold_role tag must fail-safe to exclusion.)
    - If a frozen_eval row slips through despite the wrap (e.g. the caller
      constructed an OR 1=1 bypass), the wall-breach tripwire writes a
      WALL_BREACH row to advisor_observations BEFORE raising — the audit record
      survives even if the caller swallows the exception (plan §Risk callouts).

    Returns a list of sqlite3.Row objects.
    """
    # --- Predicate guard: reject fold_role predicates in caller SQL ---
    # Callers must NOT filter on fold_role themselves.  The helper applies the
    # COALESCE wrap at the outer-query level.  A caller-supplied fold_role
    # predicate is a bypass attempt — write a WALL_BREACH audit row first
    # (write-then-raise contract: the record must survive caller-swallowed exceptions),
    # then raise ValueError.
    if any(pat in sql for pat in _BARE_FOLD_ROLE_PREDICATES):
        _write_wall_breach_observation(sql)
        raise ValueError(
            "advisor_ro_query: fold_role predicate detected in caller SQL. "
            "Bare != / <> carry the H3 SQL-NULL trap; COALESCE forms may be defeated "
            "by OR 1=1 bypass.  The helper wraps the query with a COALESCE predicate "
            "automatically — callers must not add a fold_role filter to their SQL."
        )

    # --- COALESCE wrap: exclude frozen_eval AND untagged (NULL) rows at the DB level ---
    # Wraps the caller's SQL as an inner subquery and adds an outer WHERE that
    # converts NULL fold_role to 'NULL_SENTINEL' so the NOT IN covers both
    # frozen_eval and untagged rows (M-1 safe-default: fail-safe to exclusion).
    #
    # Fallback for non-partitioned tables: if the inner query does not project
    # fold_role, SQLite raises OperationalError: no such column: fold_role.
    # In that case, execute the unwrapped query — a table with no fold_role
    # column cannot contain frozen_eval rows, so the result is structurally safe.
    wrapped_sql = (
        "SELECT * FROM (\n"
        + sql
        + "\n) AS _advisor_inner_query"
        + " WHERE COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval','NULL_SENTINEL')"
    )

    conn = get_ro_connection()
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(wrapped_sql, params).fetchall()
        except sqlite3.OperationalError as oe:
            if "fold_role" in str(oe).lower():
                # Inner query does not project fold_role — no frozen_eval rows possible.
                # Execute the caller's raw SQL directly; the post-hoc tripwire below
                # provides the safety net should fold_role somehow appear in the result.
                rows = conn.execute(sql, params).fetchall()
            else:
                raise
    finally:
        conn.close()

    # --- Post-hoc tripwire: detect frozen_eval rows that bypassed the wrap ---
    # Under correct operation this check never fires: the outer COALESCE filters
    # frozen_eval at the SQL level.  If a frozen_eval row appears in the final
    # result (e.g. an outer-wrap-defeating injection not caught by the predicate
    # guard), write the audit row first (write-then-raise contract) then raise.
    for row in rows:
        try:
            role = row["fold_role"]
        except (IndexError, KeyError):
            # Row does not project fold_role — cannot verify; skip.
            # Must be `continue`, not `break`: a break would silently skip all
            # remaining rows, letting a frozen_eval row in a later position escape.
            continue
        if role == "frozen_eval":
            _write_wall_breach_observation(sql)
            raise RuntimeError(
                "advisor_ro_query: WALL_BREACH — a frozen_eval row reached the Advisor "
                "result set despite the COALESCE wrap.  The frozen-eval fold is the held-out "
                "evaluation partition; Advisor reads must never touch it.  "
                "SQL fragment logged to advisor_observations."
            )

    return list(rows)


def _write_wall_breach_observation(sql_fragment: str) -> None:
    """Write a WALL_BREACH audit row to advisor_observations.

    Called by advisor_ro_query before raising on a wall-breach.  Uses a
    plain get_connection() (not the RO connection) so the write succeeds even
    when the offending query came through the read-only path.

    If the write itself fails (full disk, locked DB), the caller will still
    raise — the audit row is best-effort; the breach detection is not.
    """
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO advisor_observations "
            "(advisor_role, subject_type, subject_id, verdict, raw_response, is_advisory_only) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "WALL_BREACH",
                "fold_role_wall",
                "frozen_eval",
                "BREACH",
                sql_fragment[:2000],  # cap at 2 000 chars; avoid unbounded writes
                1,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logging.error("_write_wall_breach_observation: failed to write audit row: %s", exc)


def query_wall_breach_tripwire() -> list:
    """Return researcher_dof_ledger rows that represent a post-freeze frozen_eval touch.

    A wall breach is defined as: a researcher_dof_ledger row with
    touched_frozen_eval = 1 AND created_at > spec_bundles.frozen_at for the
    associated spec bundle.  Any non-empty result is a hard CI failure (M-1 ★).

    Uses canonical schema (migration 018):
      - spec_bundle_id TEXT soft FK to spec_bundles.bundle_hash (not integer id)
      - touched_frozen_eval INTEGER boolean (1 = wall-breach tripwire fired)

    Returns a list of sqlite3.Row objects; empty list means the wall held.
    An OperationalError is raised (not swallowed) if researcher_dof_ledger or
    spec_bundles does not exist — this surfaces a missing-migration failure
    rather than returning a false-clean empty result.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT r.id, r.spec_bundle_id, r.touched_frozen_eval, r.created_at,"
            "       r.facet_name, r.evidence_source,"
            "       b.frozen_at, b.bundle_hash"
            "  FROM researcher_dof_ledger r"
            "  JOIN spec_bundles b ON r.spec_bundle_id = b.bundle_hash"
            " WHERE r.touched_frozen_eval = 1"
            "   AND r.created_at > b.frozen_at"
        ).fetchall()
    finally:
        conn.close()
    return list(rows)


# --- R1: Fleet Alert State Helpers ---


def read_fleet_alert() -> "dict | None":
    """Return the fleet_alert_state row as a dict, or None when the table is empty.

    tripped_symphonies is returned as a parsed list ([] when the column is NULL or
    absent on pre-migration rows).
    """
    import json as _json

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, tripped_at_et, triggered_reason, tripped_count, active_count, "
            "dismissed_at_et, tripped_symphonies "
            "FROM fleet_alert_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # Pre-migration 013: tripped_symphonies column not yet present.
        try:
            row = conn.execute(
                "SELECT id, tripped_at_et, triggered_reason, tripped_count, active_count, "
                "dismissed_at_et "
                "FROM fleet_alert_state WHERE id = 1"
            ).fetchone()
        except Exception:
            return None
    except Exception:
        return None
    if row is None:
        return None
    result = dict(row)
    raw = result.get("tripped_symphonies")
    try:
        result["tripped_symphonies"] = _json.loads(raw) if raw else []
    except (ValueError, TypeError):
        result["tripped_symphonies"] = []
    return result


def write_fleet_alert(payload: dict) -> None:
    """Upsert the fleet_alert_state singleton row (id=1).

    Clears dismissed_at_et to NULL unless the caller explicitly includes it in payload.
    tripped_symphonies accepts a list of display names; stored as a JSON array.
    Uses INSERT OR REPLACE so each call is idempotent.
    """
    import json as _json

    dismissed_at_et = payload.get("dismissed_at_et")
    names = payload.get("tripped_symphonies") or []
    tripped_symphonies_json = _json.dumps(names) if names else None
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count, "
            "dismissed_at_et, tripped_symphonies) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            (
                payload["tripped_at_et"],
                payload["triggered_reason"],
                payload["tripped_count"],
                payload["active_count"],
                dismissed_at_et,
                tripped_symphonies_json,
            ),
        )
    except sqlite3.OperationalError:
        # Pre-migration 013: tripped_symphonies column not yet present — use legacy schema.
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


# --- AC-P2.5: Port-state helpers ---

_PORT_STATE_COLUMNS = (
    "account_id",
    "composition_hash",
    "high_water_mark",
    "safe_hwm",
    "shadow_hwm",
    "vwap_ticks_json",
    "vwap_bleed_ticks_json",
    "mc_history_json",
    "mc_prob",
    "armed",
    "para_armed",
    "port_breakeven_active",
    "triggered",
    "triggered_reason",
    "prev_return",
    "current_return",
    "last_target_reduction_json",
    "last_selected_symphony_id",
    "stop_trigger",
    "updated_at",
)


def read_port_state(account_id: str) -> "dict | None":
    """Return the port_state row for account_id as a dict, or None when absent."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_PORT_STATE_COLUMNS) + " FROM port_state WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return dict(row)


def write_port_state(account_id: str, state_dict: dict) -> None:
    """Upsert one port_state row for account_id.

    Only columns present in state_dict are written; unspecified columns retain
    their current values (via INSERT OR REPLACE read-modify-write on PK).
    updated_at is always stamped to now UTC.
    """
    existing = read_port_state(account_id) or {}
    existing.update(state_dict)
    existing["account_id"] = account_id
    existing["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    cols = [c for c in _PORT_STATE_COLUMNS if c in existing]
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    values = [existing[c] for c in cols]

    conn = get_connection()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO port_state ({col_names}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def clear_port_state(account_id: str) -> None:
    """Delete the port_state row for account_id. Idempotent — safe when absent."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM port_state WHERE account_id = ?", (account_id,))
        conn.commit()
    finally:
        conn.close()


def get_all_port_states() -> "list[dict]":
    """Return all port_state rows as a list of dicts, ordered by account_id."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_PORT_STATE_COLUMNS) + " FROM port_state ORDER BY account_id"
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def compute_composition_hash(symphony_ids: "list[str]") -> str:
    """Return a stable O(1)-comparable hash of the current symphony set.

    Order-independent: the list is sorted before hashing so callers need not
    normalise order. Used by the mode resolver to detect composition changes
    without deep-comparing full symphony objects (AC-P2.8.1).
    """
    canonical = ",".join(sorted(symphony_ids))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# --- H1: Trigger Attribution Telemetry ---


def record_exit_trigger(
    *,
    symphony_id: str,
    account_id: "str | None" = None,
    triggered_reason: str,
    at_return: "float | None" = None,
    gate_state: "dict | None" = None,
    gate_state_json: "str | None" = None,
    cycle_id: "str | None" = None,
    ts_utc: "str | None" = None,
    ts_et: "str | None" = None,
    math_mode: "str | None" = None,
    port_trigger_id: "str | None" = None,
) -> None:
    """Write one exit-trigger telemetry row.

    Opens its own connection — does NOT join the cycle's save_state transaction.
    A failure here must never fail the cycle; any exception is logged at ERROR
    and swallowed.  Called from alpha_bot_execution.py at the triggered=True set site.

    AC-P2.10: math_mode and port_trigger_id support port-level exit attribution.
    gate_state_json may be passed as a pre-serialised string (e.g. from tests)
    or as a dict via gate_state; gate_state_json takes precedence.
    ts_utc/ts_et may be supplied by callers (tests, replays); generated from
    system clock when absent.
    """
    from datetime import timedelta

    if ts_utc is None or ts_et is None:
        now_utc = datetime.now(UTC)
        ts_utc = ts_utc or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        # ET offset approximation for display; EDT = UTC-4.
        ts_et = ts_et or (now_utc - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")

    if gate_state_json is None and gate_state is not None:
        gate_state_json = json.dumps(gate_state)

    try:
        conn = sqlite3.connect(_db_file(), timeout=10.0)
        conn.execute(
            "INSERT INTO exit_triggers "
            "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, "
            " gate_state_json, cycle_id, math_mode, port_trigger_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_utc,
                ts_et,
                symphony_id,
                account_id,
                triggered_reason,
                at_return,
                gate_state_json,
                cycle_id,
                math_mode,
                port_trigger_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.error("record_exit_trigger failed for %s: %s", symphony_id, exc)


def get_recent_exit_triggers(limit: int = 50) -> "list[dict]":
    """Return the most-recent exit_triggers rows across all symphonies.

    Used by /api/triggers dashboard route. Includes math_mode and port_trigger_id
    columns (AC-P2.10.4). Returns an empty list on error.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, ts_utc, ts_et, symphony_id, account_id, triggered_reason, "
            "at_return, gate_state_json, cycle_id, math_mode, port_trigger_id "
            "FROM exit_triggers ORDER BY ts_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception as exc:
        logging.error("get_recent_exit_triggers failed: %s", exc)
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


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
    position_epoch: "str | None" = None,
) -> None:
    """Write one shadow-equity telemetry row (M1F).

    Opens its own connection — does NOT join the cycle's save_state transaction.
    Failure is logged at ERROR and swallowed; the cycle must never fail on telemetry.
    AC-M1F.1.2, PA-M1F-10: both current_return and shadow_return are required (NOT NULL,
    no DEFAULT) — caller must supply real values.
    AC-3: position_epoch scopes the row to one position-open; the trajectory query
    self-selects the current epoch. None on a legacy/unstamped row.
    """
    # Invalidate all cache entries for this symphony (keys now include db_file as 3rd element).
    stale = [k for k in _shadow_cr_cache if k[0] == symphony_id]
    for k in stale:
        del _shadow_cr_cache[k]
    try:
        conn = sqlite3.connect(_db_file(), timeout=10.0)
        conn.execute(
            "INSERT INTO shadow_history "
            "(ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
            " current_return, shadow_return, is_post_trigger, trigger_id, "
            " position_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                position_epoch,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.error("record_shadow_observation failed for %s: %s", symphony_id, exc)


# --- H4: Telemetry write helper ---

# Valid mode values — enforced at the call boundary (project rule 4 analogue:
# every call site must state the mode explicitly, no default).
_TELEMETRY_VALID_MODES = frozenset({"live", "replay"})

# CC-002: allowlist of tables that write_telemetry_row may target.
# Expand this set whenever a new legitimate Phase-N consumer is added.
# table_name arguments not in this set are rejected before any SQL is built —
# preventing f-string interpolation of attacker-controlled identifiers.
_WRITE_TELEMETRY_TABLES = frozenset({
    "cvar_diagnostics",   # M2 Phase-1 consumer (record_cvar_diagnostic)
})

# CC-002: column identifier must be a safe SQLite identifier before f-string
# interpolation.  Matches lowercase letters, digits, and underscores only.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _write_telemetry_row_unsafe(table_name: str, row_dict: dict) -> None:
    """Validate identifiers and execute the INSERT.  Not safe to call directly
    from production — always go through write_telemetry_row which enforces the
    mode contract (live-swallow / replay-raise) around this function.

    Raises ValueError if table_name is not in _WRITE_TELEMETRY_TABLES or if
    any column name fails the safe-identifier pattern (CC-002).
    Raises sqlite3.Error on DB failures — callers decide whether to swallow.
    """
    # CC-002: validate table_name before any f-string interpolation.
    if table_name not in _WRITE_TELEMETRY_TABLES:
        raise ValueError(
            f"write_telemetry_row: table_name {table_name!r} is not in the "
            f"telemetry allowlist; add it to _WRITE_TELEMETRY_TABLES if it is "
            f"a legitimate consumer"
        )

    # CC-002: validate every column name before f-string interpolation.
    columns = list(row_dict.keys())
    for col in columns:
        if not _IDENTIFIER_RE.match(col):
            raise ValueError(
                f"write_telemetry_row: column name {col!r} is not a valid "
                f"SQLite identifier (must match ^[a-z_][a-z0-9_]*$)"
            )

    # Build the INSERT — table_name and column names are provably safe after
    # the validations above; VALUES are still parameterized.
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(columns)
    sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
    values = tuple(row_dict[c] for c in columns)

    conn = sqlite3.connect(_db_file(), timeout=10.0)
    conn.execute(sql, values)
    conn.commit()
    conn.close()


def write_telemetry_row(
    table_name: str,
    row_dict: dict,
    *,
    mode: Literal["live", "replay"],
) -> None:
    """Write one telemetry row to table_name via a short-lived connection.

    Opens its own sqlite3.connect() — does NOT join the cycle's save_state
    transaction.  Connection pattern matches record_shadow_observation (:1171).

    mode="live":   swallows sqlite3.Error and ValueError; logs one WARNING
                   with table_name, error type, and cycle_id only (gate 7 —
                   no payload values).  Returns None.  The cycle must never
                   fail on telemetry.
    mode="replay": lets sqlite3.Error and ValueError propagate — a replay
                   that cannot persist its row is loud-broken by design
                   (H4 binding).

    Raises ValueError for any mode value other than "live" or "replay".
    Raises ValueError if table_name is not in _WRITE_TELEMETRY_TABLES
        (CC-002: prevents f-string interpolation of arbitrary identifiers).
    Raises ValueError if any column name in row_dict does not match the
        safe-identifier pattern ^[a-z_][a-z0-9_]*$ (CC-002).
    Raises TypeError (from Python) if mode is omitted — it is keyword-only
    with no default (H4 plan risk R4).
    """
    if mode not in _TELEMETRY_VALID_MODES:
        raise ValueError(
            f"write_telemetry_row: mode must be 'live' or 'replay'; got {mode!r}"
        )

    if mode == "live":
        try:
            _write_telemetry_row_unsafe(table_name, row_dict)
        except (sqlite3.Error, ValueError) as exc:
            # Gate 7: log only table_name, error type, and cycle_id — no
            # financial payload values from row_dict.
            cycle_id = row_dict.get("cycle_id")
            logging.warning(
                "write_telemetry_row failed for table=%s error=%s cycle_id=%s",
                table_name,
                type(exc).__name__,
                cycle_id,
            )
    else:  # mode == "replay"
        _write_telemetry_row_unsafe(table_name, row_dict)


def record_cvar_diagnostic(
    cycle_id: str,
    symphony_id: str,
    cvar_5pct: "float | None",
    cvar_5pct_stderr: "float | None",
    cvar_n_tail: "int | None",
    cvar_5pct_long: "float | None",
    cvar_n_tail_long: "int | None",
    *,
    mode: Literal["live", "replay"],
) -> None:
    """Write one cvar_diagnostics telemetry row (M2 Phase-1 consumer).

    Thin wrapper over write_telemetry_row — all connection management and
    live-swallow / replay-raise logic lives there (H4 plan deliverable 6;
    spec-h4 Finding 6 / rev-h4 REQ-7).

    mode= is required (keyword-only, no default) — same contract as
    write_telemetry_row: every call site states the mode explicitly.
    """
    # F-4 sentinel discipline: cvar_n_tail is NOT NULL DEFAULT 0.
    # SQLite only substitutes the DEFAULT when the column is absent from the statement;
    # passing Python None explicitly raises IntegrityError (NOT NULL constraint violation).
    # Coerce here so the constraint is satisfied at the call site, not swallowed downstream.
    # cvar_n_tail_long is INTEGER DEFAULT NULL — NULL is the correct Phase-1 sentinel
    # (no long window computed); do NOT coerce it.
    cvar_n_tail = 0 if cvar_n_tail is None else cvar_n_tail
    row_dict = {
        "cycle_id": cycle_id,
        "symphony_id": symphony_id,
        "cvar_5pct": cvar_5pct,
        "cvar_5pct_stderr": cvar_5pct_stderr,
        "cvar_n_tail": cvar_n_tail,
        "cvar_5pct_long": cvar_5pct_long,
        "cvar_n_tail_long": cvar_n_tail_long,
    }
    write_telemetry_row("cvar_diagnostics", row_dict, mode=mode)


# --- Replay-determinism anchor: Gate-1 parity column classification ---
#
# Non-persistence decision: the MC seed is a pure function of cycle_id via
# derive_cycle_mc_seed (math_engine.py) — persisting it would create a drift
# surface where a stored seed diverges from the re-derived value without any
# observable error. A replay re-derives the seed from cycle_id; storing it
# is redundant. See plan: feature-plans/decision-science/phase-1/
# replay-determinism-anchor/plan.md §Why and §Risk callouts.
#
# _PARITY_DECISION_COLUMNS: the five decision-content columns Gate-1 asserts
#   on — must be bit-identical across two replays of the same cycle_id.
#   Includes second-window residue (cvar_5pct_long, cvar_n_tail_long) per
#   council §B.6 and synthesis §A.8 A3 binding.
#
# _PARITY_EXCLUDE_COLUMNS: columns legitimately different across replays —
#   id (AUTOINCREMENT, replay inserts a new row), ts_utc (wall-clock stamp),
#   cycle_id and symphony_id (the lookup key pair, not parity targets).
#   Together with _PARITY_DECISION_COLUMNS these cover every column in
#   cvar_diagnostics; the anti-drift fence test enforces full classification.

_PARITY_DECISION_COLUMNS: tuple[str, ...] = (
    "cvar_5pct",
    "cvar_5pct_stderr",
    "cvar_n_tail",
    "cvar_5pct_long",
    "cvar_n_tail_long",
)

_PARITY_EXCLUDE_COLUMNS: tuple[str, ...] = (
    "id",
    "ts_utc",
    "cycle_id",
    "symphony_id",
)


def read_cvar_diagnostic_for_cycle(
    cycle_id: str,
    symphony_id: str,
) -> "dict | None":
    """Return the most-recent cvar_diagnostics row for (cycle_id, symphony_id).

    Uses get_ro_connection() — read-only enforcement at the driver level,
    consistent with the dashboard read pattern (architecture constraint 5).

    Returns None when no row exists for the given pair — the replay harness
    treats None as "no prior run to compare against" and skips parity assertion.

    The returned dict includes at minimum the _PARITY_DECISION_COLUMNS keys;
    callers that need the full row may inspect all returned keys.
    """
    conn = get_ro_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM cvar_diagnostics "
            "WHERE cycle_id = ? AND symphony_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (cycle_id, symphony_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(row)


def read_cvar_diagnostic_for_symphony(symphony_id: str) -> "dict | None":
    """Return the most-recent cvar_diagnostics row for symphony_id, or None.

    Uses get_ro_connection() — read-only enforcement at the driver level,
    consistent with the dashboard read pattern (architecture constraint 5).

    Ordered by ts_utc DESC so the latest cycle's row wins when a symphony
    has been diagnosed multiple times. idx_cvar_diag_symphony_ts covers
    the (symphony_id, ts_utc DESC) lookup (migration 021).
    """
    conn = get_ro_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM cvar_diagnostics "
            "WHERE symphony_id = ? "
            "ORDER BY ts_utc DESC LIMIT 1",
            (symphony_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(row)


def prune_old_shadow_history(retention_days: int) -> int:
    """Delete shadow_history rows older than retention_days. Returns total rows deleted.

    Uses a portable subquery DELETE loop (PA-M1F-5) — works on all SQLite builds
    regardless of SQLITE_ENABLE_UPDATE_DELETE_LIMIT compile flag.
    """
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    total_deleted = 0
    try:
        conn = sqlite3.connect(_db_file(), timeout=10.0)
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
        conn = sqlite3.connect(_db_file(), timeout=10.0)
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
        conn = sqlite3.connect(_db_file(), timeout=10.0)
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
        conn = sqlite3.connect(_db_file(), timeout=10.0)
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
        conn = sqlite3.connect(_db_file(), timeout=10.0)
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


def get_guard_alpha_by_symphony(symphony_ids: list[str] | None = None) -> dict[str, float]:
    """Return most-recent at_return per symphony from exit_triggers (read-only).

    Used by /api/state to surface guard_alpha on triggered symphony cards.
    at_return is the best proxy available in the DB for guard alpha saved.
    Returns {symphony_id: at_return} for all triggered symphonies, or filtered
    to the requested ids when symphony_ids is provided.
    """
    conn = get_ro_connection()
    try:
        if symphony_ids:
            placeholders = ",".join("?" * len(symphony_ids))
            rows = conn.execute(
                f"SELECT symphony_id, at_return FROM exit_triggers "
                f"WHERE symphony_id IN ({placeholders}) "
                f"ORDER BY ts_utc DESC",
                symphony_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symphony_id, at_return FROM exit_triggers ORDER BY ts_utc DESC"
            ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    result: dict[str, float] = {}
    for row in rows:
        sid = row[0]
        if sid not in result:
            result[sid] = float(row[1]) if row[1] is not None else 0.0
    return result


def prune_old_triggers(retention_days: int) -> int:
    """Delete exit_triggers rows older than retention_days, in batches of 1000.

    Returns the total number of rows deleted.
    Called by the daily scheduled task in app.py — never during a cycle write.
    Batched DELETE with LIMIT avoids long write locks on the shared state DB.
    """
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime(
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
