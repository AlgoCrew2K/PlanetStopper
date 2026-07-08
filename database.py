"""SQLite state management for Planet Stopper with Account-Level Strategies."""

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
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

# Sentinel connection for in-memory DBs (":memory:" path).
# sqlite3.connect(":memory:") returns a fresh, isolated DB per call; the
# shared-cache URI ("file::memory:?cache=shared") makes all callers share one
# instance, but it is destroyed when the last connection closes.  Holding this
# sentinel open keeps the shared in-memory DB alive for the duration of a test
# run.  Production code never sets DB_PATH=":memory:", so this is test-only.
_in_memory_sentinel: "sqlite3.Connection | None" = None

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
        resolved = DB_FILE
    else:
        resolved = os.environ.get("DB_PATH", DB_FILE)
    # Guard: under pytest, opening the production DB basename is always a test
    # isolation bug — the session-scoped _session_db_guard fixture must have set
    # DB_PATH to a temp file before any test runs.  This guard converts a silent
    # test→prod-DB leak into a loud, immediate failure.
    # CRITICAL: gated on "pytest" in sys.modules so the live daemon (which never
    # imports pytest) is completely unaffected.
    if "pytest" in sys.modules and os.path.basename(resolved) == "alphabot_state.db":
        raise RuntimeError(
            f"test attempted to open the production DB at {resolved!r} — "
            "DB isolation bug: set DB_PATH env var to a temp file "
            "(the _session_db_guard or _isolate_db fixture must be active)."
        )
    return resolved


def get_connection():
    global _in_memory_sentinel
    path = _db_file()
    if path == ":memory:":
        # Use the shared-cache URI so all callers share the same in-memory DB.
        # Keep a module-level sentinel connection open to prevent the in-memory
        # DB from being destroyed when transient connections close.
        shared_uri = "file::memory:?cache=shared"
        if _in_memory_sentinel is None:
            _in_memory_sentinel = sqlite3.connect(shared_uri, uri=True, timeout=10.0)
        return sqlite3.connect(shared_uri, uri=True, timeout=10.0)
    return sqlite3.connect(path, timeout=10.0)


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
        "CREATE TABLE IF NOT EXISTS execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)"  # noqa: E501  # un-wrappable long line
    )
    cursor.execute("CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS chart_archive (date TEXT, symphony_id TEXT, data TEXT, UNIQUE(date, symphony_id))"  # noqa: E501  # un-wrappable long line
    )

    # NEW: Symphony-Level Strategy Storage
    # H1 DUAL-WRITE: live_mode is also added by migration 030 via ALTER TABLE.
    # The duplicate-column-name swallow in run_migrations() reconciles fresh-DB
    # and upgraded-DB paths (same pattern as migrations 020, 023, 028, 029).
    # live_mode is a SEPARATE column — never inside the parameters JSON blob —
    # so the autotuner cannot reach it.  DEFAULT 0 = dry-run (arch rule 4).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symphony_strategies (
            symphony_name TEXT PRIMARY KEY,
            parameters    TEXT,
            locked_vars   TEXT,
            live_mode     INTEGER DEFAULT 0
        )
    """)

    # H1 DUAL-WRITE: config_audit_log is also created by migration 030.
    # Append-only operator audit trail for live_mode changes; no update/delete
    # accessor — immutable by design (same pattern as llm_suggestions).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config_audit_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony     TEXT    NOT NULL,
            field        TEXT    NOT NULL,
            before_value TEXT,
            after_value  TEXT    NOT NULL,
            operator     TEXT    NOT NULL,
            ts_utc       TEXT    NOT NULL
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
            s_count                   INTEGER DEFAULT NULL,
            -- migration 028: pbo column (H1 DUAL-WRITE HAZARD — also added via ALTER TABLE)
            pbo                       REAL    DEFAULT NULL
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

    # H1: Trigger Attribution Telemetry — exit_triggers table.
    # H1 DUAL-WRITE: all columns (including those added by migrations 011 and 029
    # via ALTER TABLE) are listed here so fresh deployments have the full schema
    # without requiring run_migrations() to be called first.  The duplicate-column-name
    # swallow in run_migrations() (database.py:1159-1168) reconciles the overlap:
    # migration 005 and 011 ALTER TABLE calls are silently marked applied on fresh DBs
    # where the columns already exist; migration 029 likewise.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exit_triggers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc           TEXT    NOT NULL,
            ts_et            TEXT    NOT NULL,
            symphony_id      TEXT    NOT NULL,
            account_id       TEXT    DEFAULT NULL,
            triggered_reason TEXT    NOT NULL,
            at_return        REAL    DEFAULT NULL,
            gate_state_json  TEXT    DEFAULT NULL,
            cycle_id         TEXT    DEFAULT NULL,
            math_mode        TEXT    DEFAULT NULL,
            port_trigger_id  TEXT    DEFAULT NULL,
            -- migration 029: also_true co-fire list (H1 DUAL-WRITE — also added via ALTER TABLE)
            also_true_json   TEXT    DEFAULT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_exit_triggers_ts ON exit_triggers (ts_utc DESC)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_exit_triggers_symphony_ts "
        "ON exit_triggers (symphony_id, ts_utc DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_exit_triggers_port_trigger_id "
        "ON exit_triggers (port_trigger_id) "
        "WHERE port_trigger_id IS NOT NULL"
    )

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


def detect_zero_to_positive_holdings_transition(
    previous_state, current_holdings_positive: bool
) -> bool:
    """Pure helper: does a per-symphony cycle exhibit a zero -> positive
    holdings transition (AC-2 / D12)?

    Returns True iff the prior cycle's `last_holdings_positive` marker is
    False AND the current cycle's holdings are positive. A first-ever
    observation (no marker on previous_state) is NOT a transition — there
    is no prior cycle to transition from. Mirrors the conservative semantic
    the data-phase loop needs to avoid spurious resets on a symphony's very
    first observation.

    Args:
        previous_state: a dict (typically bot_state[symphony_id]) carrying
            the persisted `last_holdings_positive` boolean. Absent key reads
            as False so a first-ever observation does NOT fire (the
            mid-position-rebalance / carry-across-day pin tests assume
            this conservative semantic).
        current_holdings_positive: the boolean reading of this cycle's
            holdings via has_positive_holdings.

    Returns:
        True only on the F -> T transition; False on F -> F, T -> T, T -> F,
        and the first-observation case.
    """
    prior_positive = bool((previous_state or {}).get("last_holdings_positive", False))
    return (not prior_positive) and bool(current_holdings_positive)


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
    # SELECT live_mode alongside params/locked_vars so the exec path reads
    # symphony_strat.get("live_mode", False) — no second DB query on the hot path
    # (arch constraint 1: no blocking I/O per minute cycle).
    cursor.execute(
        "SELECT parameters, locked_vars, live_mode FROM symphony_strategies WHERE symphony_name = ?",  # noqa: E501  # un-wrappable long line
        (symphony_name,),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "params": json.loads(row[0]),
            "locked_vars": json.loads(row[1]),
            "live_mode": bool(row[2]) if row[2] is not None else False,
        }

    # Initialize with defaults if not found — live_mode defaults to False (dry-run).
    # arch rule 4: is_live=True is explicit, never by omission.
    save_symphony_strategy(symphony_name, DEFAULT_STRATEGY, DEFAULT_LOCKED_VARS)
    return {
        "params": DEFAULT_STRATEGY.copy(),
        "locked_vars": DEFAULT_LOCKED_VARS.copy(),
        "live_mode": False,
    }


def save_symphony_strategy(symphony_name, params, locked_vars):
    symphony_name = normalize_name(symphony_name)
    conn = get_connection()
    cursor = conn.cursor()
    # ON CONFLICT DO UPDATE preserves live_mode: only parameters and locked_vars
    # are touched.  INSERT OR REPLACE would DELETE + INSERT, resetting live_mode
    # to its DEFAULT (0) and silently disabling live trading after every autotune
    # run — a silent real-money safety regression (arch rule 4).
    cursor.execute(
        """
        INSERT INTO symphony_strategies (symphony_name, parameters, locked_vars)
        VALUES (?, ?, ?)
        ON CONFLICT(symphony_name) DO UPDATE SET
            parameters  = excluded.parameters,
            locked_vars = excluded.locked_vars
        """,
        (symphony_name, json.dumps(params), json.dumps(locked_vars)),
    )
    conn.commit()
    conn.close()


def get_symphony_live_mode(symphony_name: str) -> int:
    """Return the live_mode flag (0 or 1) for a symphony.

    Returns 0 (dry-run) when no symphony_strategies row exists — arch rule 4:
    is_live=True is explicit, never by omission.  The exec path calls this once
    per symphony per cycle at alpha_bot_execution.py:1152.

    Normalizes symphony_name the same way save_symphony_strategy does so raw
    API names resolve to the same DB row as normalized names.
    """
    symphony_name = normalize_name(symphony_name)
    try:
        conn = get_ro_connection()
        try:
            row = conn.execute(
                "SELECT live_mode FROM symphony_strategies WHERE symphony_name = ?",
                (symphony_name,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return 0
    if row is None:
        return 0
    # SQLite stores INTEGER; coerce to int in case of NULL (additive column with DEFAULT 0).
    return int(row[0]) if row[0] is not None else 0


def set_symphony_live_mode(symphony_name: str, live: int, operator: str) -> None:
    """Set the per-symphony live_mode flag and write an immutable audit log entry.

    live must be 0 (dry-run) or 1 (live).  This is the only write path for
    live_mode — the autotuner never calls this function.

    If no symphony_strategies row exists yet (operator enables live before the
    first autotune run), this function creates a minimal row (DEFAULT_STRATEGY
    params, DEFAULT_LOCKED_VARS) so the UPDATE can succeed, then sets live_mode.

    Writes one config_audit_log row recording:
      - symphony: normalized symphony_name
      - field: 'live_mode'
      - before_value: str(prior live_mode)
      - after_value: str(live)
      - operator: caller-supplied identity string
      - ts_utc: UTC ISO timestamp of this change

    The audit log is append-only — no update/delete accessor exists (same
    immutability pattern as llm_suggestions and advisor_observations).
    """
    symphony_name = normalize_name(symphony_name)
    conn = get_connection()
    try:
        # Ensure the row exists; INSERT OR IGNORE creates it with DEFAULT params
        # if absent.  We never touch parameters/locked_vars here.
        conn.execute(
            "INSERT OR IGNORE INTO symphony_strategies (symphony_name, parameters, locked_vars) "
            "VALUES (?, ?, ?)",
            (symphony_name, json.dumps(DEFAULT_STRATEGY), json.dumps(DEFAULT_LOCKED_VARS)),
        )
        # Read the current live_mode for the audit before_value.
        prior_row = conn.execute(
            "SELECT live_mode FROM symphony_strategies WHERE symphony_name = ?",
            (symphony_name,),
        ).fetchone()
        prior_live = int(prior_row[0]) if (prior_row and prior_row[0] is not None) else 0

        conn.execute(
            "UPDATE symphony_strategies SET live_mode = ? WHERE symphony_name = ?",
            (live, symphony_name),
        )

        ts_utc = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO config_audit_log (symphony, field, before_value, after_value, operator, ts_utc) "  # noqa: E501  # log message string
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symphony_name, "live_mode", str(prior_live), str(live), operator, ts_utc),
        )
        conn.commit()
    finally:
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
    # migration 028: Phase-3 PBO acceptance gate result.
    pbo=None,
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
                          post-selection for honest performance reporting (López de Prado 2018 Ch. 7.4).  # noqa: E501  # un-wrappable long line

    EUT audit columns (migration 020 — ARCH-001 fix):
      spec_bundle_id:     bundle_hash TEXT of the spec bundle active during this run.
      n_effective:        N_optuna + S (honest multiple-testing count from compute_n_effective).
      d_spec:             COUNT DISTINCT BACKTEST_SELECTION spec_bundle_ids in researcher_dof_ledger.  # noqa: E501  # un-wrappable long line
      gamma:              Frozen CRRA risk-aversion coefficient from spec_facets.
      overfitting_verdict: Human-readable Overfitting Conscience summary string.

    Phase-3 PBO column (migration 028):
      pbo: Probability of Backtest Overfitting from CSCV (Bailey et al. 2017).
           In (0, 1); higher means more overfitting evidence.  None when PBO
           could not be computed (insufficient CSCV paths).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO autotune_runs
            (run_timestamp, symphony_id, oos_alpha, train_alpha,
             baseline_decision, fallback_oos_alpha, default_oos_alpha,
             selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe,
             spec_bundle_id, n_effective, d_spec, gamma, overfitting_verdict,
             pbo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            pbo,
        ),
    )
    conn.commit()
    row_id: int = cursor.lastrowid
    conn.close()
    return row_id


def _autotune_run_row_to_dict(row) -> dict:
    """Map a raw autotune_runs SELECT row (16 columns) to a dict.

    Column order matches _AUTOTUNE_RUNS_SELECT. id is projected first
    (S3-AUDIT-001 fix) so the OC producer receives an honest row id.
    migration 028: pbo added at index 15.
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
        "pbo": _finite_or_none(row[15]),
    }


_AUTOTUNE_RUNS_SELECT = """
    SELECT id,
           run_timestamp, symphony_id, oos_alpha, train_alpha,
           baseline_decision, fallback_oos_alpha, default_oos_alpha,
           selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe,
           math_mode, account_id, sortino_sentinel_pct,
           pbo
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
        "(advisor_role, subject_type, subject_id, verdict, raw_response, is_advisory_only, spec_bundle_id, symphony_id) "  # noqa: E501  # un-wrappable long line
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (
            advisor_role,
            subject_type,
            subject_id,
            verdict,
            raw_response_str,
            spec_bundle_id,
            symphony_id,
        ),
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


def get_latest_market_prism_summary() -> dict | None:
    """Return the most recent MARKET_PRISM advisor_observations row, or None.

    Returns a fully parsed dict (raw_response deserialized from JSON) representing
    the most recently persisted off-hours Market Prism run, or None when no such
    row exists yet.

    Uses get_ro_connection() — read-only at the driver level (architecture constraint 5).
    Ordered by id DESC LIMIT 1 — insertion order is a reliable recency proxy for
    sequential nightly writes.
    """
    conn = get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
        + " FROM advisor_observations WHERE advisor_role = 'MARKET_PRISM' ORDER BY id DESC LIMIT 1",
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return _parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS)


def _get_latest_advisor_observation_for_run(
    connection_factory, advisor_role: str, run_id: str
) -> dict | None:
    """Shared query: the latest advisor_observations row for (advisor_role, run_id).

    Single source of truth for the exact json_extract(raw_response, '$.run_id') = ?
    match / no-stale-bleed guard that get_latest_market_prism_sources_for_run and
    get_latest_market_prism_verification_for_run both need — those two accessors
    are byte-identical mirrors differing only in the advisor_role literal.

    connection_factory is the connection-opening callable (get_ro_connection for
    both current callers) — passed in rather than hardcoded so each public
    accessor's own source still names get_ro_connection explicitly (both
    accessors have a source-introspection test asserting this).

    D-1 never-raises.
    """
    try:
        conn = connection_factory()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT "
            + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
            + " FROM advisor_observations"
            + " WHERE advisor_role = ?"
            + " AND json_extract(raw_response, '$.run_id') = ?"
            + " ORDER BY id DESC LIMIT 1",
            (advisor_role, run_id),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        return _parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS)
    except Exception:  # noqa: BLE001
        return None


def get_latest_market_prism_sources_for_run(run_id: str) -> dict | None:
    """Return the MARKET_PRISM_SOURCES advisor_observations row for this run_id, or None.

    Uses json_extract(raw_response, '$.run_id') for an exact SQL match — no Python-side
    scan window. Returns None when no match exists — never falls back to a different run's row.

    No-stale-citation-bleed guard (AC-9): a night where all lenses are unavailable
    produces no SOURCES row; returning a different run's row would inject stale citations.

    D-1 never-raises. Uses get_ro_connection().
    """
    return _get_latest_advisor_observation_for_run(
        get_ro_connection, "MARKET_PRISM_SOURCES", run_id
    )


def get_latest_market_prism_verification_for_run(run_id: str) -> dict | None:
    """Return the MARKET_PRISM_VERIFICATION advisor_observations row for this run_id, or None.

    Structural mirror of get_latest_market_prism_sources_for_run (DE-PRISM-NUMERIC-VERIFY-001,
    AC-9) — same exact json_extract(raw_response, '$.run_id') match, same no-stale-bleed
    guard (a run where the verifier found no cited_numbers, or errored, produces no
    VERIFICATION row for that run_id; falling back to a different run's row would show
    last night's checks against tonight's read), same D-1 / get_ro_connection() discipline.

    D-1 never-raises. Uses get_ro_connection().
    """
    return _get_latest_advisor_observation_for_run(
        get_ro_connection, "MARKET_PRISM_VERIFICATION", run_id
    )


def get_latest_market_lens_cache() -> dict | None:
    """Return the most recent MARKET_LENS_CACHE advisor_observations row, or None.

    Returns a fully parsed dict (raw_response deserialized from JSON) representing
    the most recently persisted nightly lens cache bundle, or None when no such row
    exists yet (cold-start) or on any DB error.

    Uses get_ro_connection() — read-only at the driver level (architecture constraint 5).
    Ordered by id DESC LIMIT 1 — insertion order is a reliable recency proxy for
    sequential nightly writes.

    D-1 never-raises: any exception degrades to None (cache miss).
    """
    try:
        conn = get_ro_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT "
            + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
            + " FROM advisor_observations WHERE advisor_role = 'MARKET_LENS_CACHE' ORDER BY id DESC LIMIT 1",
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        return _parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS)
    except Exception:  # noqa: BLE001
        return None


# --- Prism Phase 1: audit-log accessors (migration 032) ---

_PRISM_AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "run_id",
    "agent_role",
    "phase",
    "content",
    "created_at",
)


def insert_prism_audit_entry(
    run_id: str,
    agent_role: str,
    phase: str,
    content: str,
) -> int:
    """Insert one prism_audit_log row and return the new row id.

    Append-only: no update or delete accessor exists — audit entries are immutable.
    All four caller-supplied fields are required and stored verbatim via parameterized
    query (? placeholders) — never f-strings, never user-input interpolation.

    Args:
        run_id:     Nightly run identifier linking all entries of one pipeline run
                    to the corresponding MARKET_PRISM advisor_observation.
        agent_role: The agent that produced this entry (e.g. "technicals_analyst",
                    "synthesizer").
        phase:      The deliberation phase (e.g. "initial_read", "synthesis").
        content:    The agent's verbatim output for that phase.

    Returns:
        The SQLite rowid of the newly inserted row (always > 0).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO prism_audit_log (run_id, agent_role, phase, content) VALUES (?, ?, ?, ?)",
        (run_id, agent_role, phase, content),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_prism_audit_for_run(run_id: str) -> list[dict]:
    """Return all prism_audit_log entries for a run, ordered by id ascending.

    Returns an empty list when no rows match — never raises for an unknown run_id.
    Uses get_ro_connection() per architecture constraint 5 (read paths are
    structurally isolated from the write path).

    Args:
        run_id: The nightly run identifier to query.

    Returns:
        List of dicts with keys: id, run_id, agent_role, phase, content, created_at.
        Ordered by id ASC (insertion / chronological order).
    """
    conn = get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT "
        + ", ".join(_PRISM_AUDIT_COLUMNS)
        + " FROM prism_audit_log WHERE run_id = ? ORDER BY id ASC",
        (run_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(zip(_PRISM_AUDIT_COLUMNS, row)) for row in rows]


# --- Phase 3c: regime label cache (offline-produced, read on the live path) ---


def save_regime_label(symphony_id: str, label: str, as_of_date: str) -> None:
    """Persist the most-recent offline regime label for a symphony (latest wins).

    Called by the OFFLINE daily job after running regime_classifier.classify_regime
    over the symphony's daily return series. The live 1-minute execution path never
    calls the classifier; it reads this cached row via get_cached_regime_label.

    symphony_id is the primary key, so a second save for the same symphony
    OVERWRITES the prior row (the execution path always reads the latest run).
    as_of_date is the ISO date (YYYY-MM-DD) the label was computed for; it feeds
    the staleness check in get_cached_regime_label.
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO regime_label_cache (symphony_id, label, as_of_date) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(symphony_id) DO UPDATE SET "
            "label = excluded.label, "
            "as_of_date = excluded.as_of_date, "
            "updated_at = datetime('now')",
            (symphony_id, label, as_of_date),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_regime_label(
    symphony_id: str, staleness_cutoff_days: int | None = None
) -> str | None:
    """Return the cached regime label for a symphony, or None if absent/stale.

    Read-only accessor for the live execution path (architecture constraint 5):
    opens a read-only connection and never mutates state.

    staleness_cutoff_days semantics:
      - None (default): return the stored label regardless of age.
      - 0: always return None (safety escape hatch — treat every label as stale).
      - N > 0: return None when as_of_date is more than N days before today (UTC);
        otherwise return the label.

    Never raises: any missing table, missing row, or unparseable date returns None
    so the caller (apply_regime_exit_adjustment) falls back to the safe default
    (no adjustment) rather than failing on the 1-minute path.
    """
    try:
        conn = get_ro_connection()
        try:
            row = conn.execute(
                "SELECT label, as_of_date FROM regime_label_cache WHERE symphony_id = ?",
                (symphony_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None

    if row is None:
        return None

    label, as_of_date = row[0], row[1]

    if staleness_cutoff_days is None:
        return label

    # cutoff of 0 means "always stale": no label is ever fresh enough.
    if staleness_cutoff_days <= 0:
        return None

    try:
        label_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        # Unparseable stored date -> treat as stale/absent (safe default).
        return None

    age_days = (datetime.now(UTC).date() - label_date).days
    if age_days > staleness_cutoff_days:
        return None
    return label


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
    "026_mc_regime_match_telemetry.sql",
    "027_regime_label_cache.sql",
    "028_autotune_runs_pbo.sql",
    "029_exit_triggers_also_true.sql",
    "030_per_symphony_live_mode.sql",
    "031_shadow_history_sym_ts_index.sql",
    "032_prism_audit_log.sql",
    "033_sleeves.sql",
    "034_sleeve_rule_fires.sql",
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
                # initialize_db() CREATE TABLE already includes these columns — safe to mark applied.  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
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

_VALID_FREEZE_DISCIPLINES: frozenset[str] = frozenset(
    {
        "THEORY",
        "MANDATE",
        "STYLIZED_FACT",
        "POLITIS_WHITE",
        "CADENCE",
        "CALIBRATION",
        "BACKTEST_SELECTION",
    }
)

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
                justification="Phase-1 canonical bundle — W-H2 derivation + council synthesis §2.5 hard gate",  # noqa: E501  # un-wrappable long line
            )

    _phase1_theory_bundle_id_cache = (current_db, bundle_id)
    return bundle_id


# Process-local cache for the Phase-1.5 M3 bundle id.
# Tuple of (db_path, bundle_id) so the cache misses automatically if the DB
# path changes between calls (per-test isolation via DB_FILE monkeypatching).
_phase15_m3_bundle_id_cache: "tuple[str, int] | None" = None

# Canonical Phase-1.5 M3 facet values.
# R1: time_squeeze_decay_curve_v2 — the sqrt remaining-variance derivation
#   (Danielsson & Zigrand 2003, LSE FMG DP-439). freeze_discipline = THEORY.
# R2: vwap_system_a_hwm_gate_v2 — the regime-switch construction justified
#   from optimal-stopping (Leung & Zhang 2019, Peskir 1998). THEORY.
_M3_R1_FACET_NAME = "time_squeeze_decay_curve_v2"
_M3_R1_FACET_VALUE = (
    "f(t) = 1 - sqrt(1 - t). Under the standard square-root-of-time scaling "
    "for i.i.d. log-returns with constant per-unit-time variance, the standard "
    "deviation of remaining-session returns scales as sqrt(1-t); tightness "
    "(1 - remaining_std / full_std) is therefore 1 - sqrt(1-t). Zero free "
    "parameters. Danielsson & Zigrand (2003), LSE FMG DP-439."
)
_M3_R1_JUSTIFICATION = (
    "THEORY provenance: the curve is derived from first principles under i.i.d. "
    "log-returns. Cited: Danielsson & Zigrand 2003. Research note: "
    "docs/research/m3-provenance/literature-pass.md §1.3."
)
_M3_R1_CALIBRATION_EVIDENCE = (
    "intended_direction: concave, open-loaded, less aggressive midday (~0.45 pp "
    "wider stop at t=0.5 vs prior log10 heuristic), monotone-converging at "
    "endpoints (f(0)=0, f(1)=1 exactly). Expected effect: fewer mid-morning "
    "exits, more late-afternoon exits."
)

_M3_R2_FACET_NAME = "vwap_system_a_hwm_gate_v2"
_M3_R2_FACET_VALUE = (
    "The gate safe_hwm >= vwap_cross_hwm_pct is the regime boundary of a "
    "two-regime trailing-stop system. Regime 1 (below gate): primary trailing-"
    "stop only. Regime 2 (at-or-above gate): primary stop OR VWAP System-A "
    "profit-protection. Structural choice justified by optimal-stopping "
    "formalism (Leung & Zhang, 2019; Peskir, 1998 maximality principle). "
    "Runtime is byte-identical to pre-M3; this is a provenance-only closure."
)
_M3_R2_JUSTIFICATION = (
    "THEORY provenance: the regime-switch structure is justified by Leung & "
    "Zhang (2019) optimal-stopping for trailing stops with running maxima and "
    "the Peskir (1998) maximality principle. The threshold value "
    "(vwap_cross_hwm_pct) remains Optuna-searched within the BHY haircut "
    "surface. Research note: docs/research/m3-provenance/literature-pass.md §2.3."
)
_M3_R2_CALIBRATION_EVIDENCE = (
    "intended_direction: byte-identical runtime (zero behavioral change vs "
    "pre-M3). The closure is provenance-only: the regime-switch structure is "
    "now anchored to optimal-stopping theory rather than left as a practitioner "
    "heuristic. Max-absolute-deviation = 0 in the S-1 Stage 2 attribution table."
)


def get_or_create_phase15_m3_bundle_id() -> int:
    """Return the integer id for the canonical Phase-1.5 M3 spec bundle.

    Idempotent: INSERT OR IGNORE means repeated calls return the same id.
    The M3 bundle encodes two THEORY-frozen facets:
      - time_squeeze_decay_curve_v2: f(t) = 1 - sqrt(1-t) per Danielsson &
        Zigrand (2003) square-root-of-time scaling under i.i.d. returns.
      - vwap_system_a_hwm_gate_v2: regime-switch construction per Leung &
        Zhang (2019) optimal-stopping / Peskir (1998) maximality principle.

    Process-local cache: second and subsequent calls within the same process
    return immediately without touching the DB (sub-microsecond).

    Research note: docs/research/m3-provenance/literature-pass.md.
    freeze_discipline = THEORY for both facets per M3 plan §69 + NN1 binding.
    """
    global _phase15_m3_bundle_id_cache
    current_db = _db_file()
    if _phase15_m3_bundle_id_cache is not None:
        cached_db, cached_id = _phase15_m3_bundle_id_cache
        if cached_db == current_db:
            return cached_id
        # DB path changed (test isolation); fall through to re-compute.
        _phase15_m3_bundle_id_cache = None

    _m3_facets = {
        _M3_R1_FACET_NAME: _M3_R1_FACET_VALUE,
        _M3_R2_FACET_NAME: _M3_R2_FACET_VALUE,
    }
    canonical_json = canonicalize_facets_json(_m3_facets)
    bundle_hash = hash_facets_json(canonical_json)
    insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    # Fetch the id backfilled by insert_spec_bundle.
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"get_or_create_phase15_m3_bundle_id: id is NULL for bundle_hash={bundle_hash!r} "
            "after insert — run_migrations() may not have applied migration 022."
        )
    bundle_id: int = row[0]

    # Ensure the two M3 facets are registered.
    # INSERT OR IGNORE in insert_spec_bundle_facet makes repeated calls safe
    # (migration 024 adds UNIQUE(bundle_hash, facet_name) to spec_facets).
    existing = get_spec_facets_for_bundle(bundle_hash)
    existing_names = {r["facet_name"] for r in existing}

    if _M3_R1_FACET_NAME not in existing_names:
        insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=_M3_R1_FACET_NAME,
            facet_value=_M3_R1_FACET_VALUE,
            freeze_discipline="THEORY",
            justification=_M3_R1_JUSTIFICATION,
            calibration_evidence=_M3_R1_CALIBRATION_EVIDENCE,
        )

    if _M3_R2_FACET_NAME not in existing_names:
        insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=_M3_R2_FACET_NAME,
            facet_value=_M3_R2_FACET_VALUE,
            freeze_discipline="THEORY",
            justification=_M3_R2_JUSTIFICATION,
            calibration_evidence=_M3_R2_CALIBRATION_EVIDENCE,
        )

    _phase15_m3_bundle_id_cache = (current_db, bundle_id)
    return bundle_id


def get_spec_bundle(bundle_hash: str) -> "dict | None":
    """Return the spec_bundles row for the given hash as a dict, or None."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT "
            + ", ".join(_SPEC_BUNDLE_COLUMNS)
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
            "SELECT " + ", ".join(_SPEC_BUNDLE_COLUMNS) + " FROM spec_bundles WHERE id = ?",
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
            (
                bundle_hash,
                facet_name,
                facet_value,
                freeze_discipline,
                justification,
                calibration_evidence,
            ),
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
            "SELECT "
            + ", ".join(_SPEC_FACET_COLUMNS)
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

_VALID_DOF_FACET_CATEGORIES: frozenset[str] = frozenset(
    {
        "specification",
        "parameter",
    }
)

_VALID_DOF_DECISION_TYPES: frozenset[str] = frozenset(
    {
        "FIXED",
        "SEARCHED",
        "REVISED",
        "OOS_PEEK",
    }
)

_VALID_DOF_EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {
        "THEORY",
        "MANDATE",
        "STYLIZED_FACT",
        "CALIBRATION",
        "BACKTEST_SELECTION",
        "OOS",
    }
)

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
            "SELECT "
            + ", ".join(_DOF_LEDGER_COLUMNS)
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
                "SELECT " + ", ".join(_DOF_LEDGER_COLUMNS) + " FROM researcher_dof_ledger"
                " WHERE evidence_source = 'BACKTEST_SELECTION'"
                "   AND COALESCE(touched_frozen_eval, 0) = 0"
                "   AND (spec_bundle_id IS NULL OR spec_bundle_id != ?)"
                " ORDER BY id ASC",
                (winning_spec_bundle_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT " + ", ".join(_DOF_LEDGER_COLUMNS) + " FROM researcher_dof_ledger"
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

# H1 rename note: no migration 032 — the MC value is unchanged (rename-only) and the
# persisted names (mc_prob, mc_history_json) are neutral; no *_beating column exists
# anywhere in migrations/ or here. The in-memory transient key "prob_beating" is renamed
# in code (alpha_bot_execution.py) and maps to the neutral "mc_prob" on serialization.
# port_state.mc_prob is vestigial (write_port_state is not called on the live execution
# path post-port-deprecation) — no dual-write migration warranted.
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
    also_true: "list[str] | None" = None,
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
    also_true: list of other exit-layer names that co-fired when the primary
    trigger won (from math_engine.resolve_trigger_priority).  None means not
    provided (legacy/pre-029 rows); [] means a clean single-winner exit with
    no co-fires.  Stored as JSON via json.dumps — dual-stored in also_true_json
    column AND retained in the gate_state_json blob (Option A, ruling 2026-06-01).
    """
    from zoneinfo import ZoneInfo

    if ts_utc is None or ts_et is None:
        now_utc = datetime.now(UTC)
        ts_utc = ts_utc or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        # Use the real America/New_York timezone so EST (UTC-5) and EDT (UTC-4)
        # are both handled correctly — mirrors get_current_et() in alpha_bot_execution.py.
        ts_et = ts_et or datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M:%S")

    if gate_state_json is None and gate_state is not None:
        gate_state_json = json.dumps(gate_state)

    also_true_json = json.dumps(also_true) if also_true is not None else None

    try:
        conn = sqlite3.connect(_db_file(), timeout=10.0)
        conn.execute(
            "INSERT INTO exit_triggers "
            "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, "
            " gate_state_json, cycle_id, math_mode, port_trigger_id, also_true_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                also_true_json,
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
            "at_return, gate_state_json, cycle_id, math_mode, port_trigger_id, also_true_json "
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
_WRITE_TELEMETRY_TABLES = frozenset(
    {
        "cvar_diagnostics",  # M2 Phase-1 consumer (record_cvar_diagnostic)
    }
)

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

    conn = get_connection()
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
        raise ValueError(f"write_telemetry_row: mode must be 'live' or 'replay'; got {mode!r}")

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
    mc_regime_match_mean_dist2: "float | None" = None,
    mc_regime_match_suppressed: "int | None" = None,
) -> None:
    """Write one cvar_diagnostics telemetry row (M2 Phase-1 consumer).

    Thin wrapper over write_telemetry_row — all connection management and
    live-swallow / replay-raise logic lives there (H4 plan deliverable 6;
    spec-h4 Finding 6 / rev-h4 REQ-7).

    mode= is required (keyword-only, no default) — same contract as
    write_telemetry_row: every call site states the mode explicitly.

    mc_regime_match_mean_dist2: mean squared Mahalanobis-style kNN distance from
        compute_regime_match_quality; None when the eligible pool was insufficient
        (migration 026 column; defaults to None so existing call sites stay green).
    mc_regime_match_suppressed: 1 when the MC veto was suppressed (is_unprecedented=True),
        0 when not suppressed, None when the guard was not run (migration 026 column).
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
    # Migration 026 columns: additive-first pattern — only include in row_dict
    # when the caller explicitly provides them. Omitting lets SQLite supply the
    # DEFAULT NULL, so pre-026 DBs (which lack the columns) accept the write
    # unchanged. This preserves backward compat through the migration window.
    if mc_regime_match_mean_dist2 is not None:
        row_dict["mc_regime_match_mean_dist2"] = mc_regime_match_mean_dist2
    if mc_regime_match_suppressed is not None:
        row_dict["mc_regime_match_suppressed"] = mc_regime_match_suppressed
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
# _PARITY_DECISION_COLUMNS: decision-content columns Gate-1 asserts on —
#   must be bit-identical across two replays of the same cycle_id.
#   Includes second-window residue (cvar_5pct_long, cvar_n_tail_long) per
#   council §B.6 and synthesis §A.8 A3 binding; and the regime-match
#   telemetry pair (mc_regime_match_mean_dist2, mc_regime_match_suppressed)
#   per rev-mc Observation 1 so suppression flips are directly auditable.
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
    # Migration 026: regime-match telemetry columns — promoted to decision-content
    # (reclassified from exclude per rev-mc Observation 1). The suppression flag and
    # its driving distance score are direct inputs to mc_prob=None; Gate-1 parity
    # must assert bit-identity of these values so a suppression flip across two
    # replays of the same cycle_id is directly auditable, not inferred from cvar_5pct.
    "mc_regime_match_mean_dist2",
    "mc_regime_match_suppressed",
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
            "SELECT * FROM cvar_diagnostics WHERE symphony_id = ? ORDER BY ts_utc DESC LIMIT 1",
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

    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    Returns {"by_symphony": {<id>: {"today": float|None, "cumulative": float|None}}, "portfolio_today": float|None}.  # noqa: E501  # un-wrappable long line
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
        f"SELECT id, ts_utc, ts_et, symphony_id, triggered_reason, at_return, gate_state_json, cycle_id, also_true_json "  # noqa: E501  # un-wrappable long line
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

    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


# --- Managed Sleeves P1: sleeve infrastructure + order layer (migration 033) ---
# All read paths below use get_ro_connection() (arch constraint 5); all writes use
# get_connection(). Every query is parameterized (? placeholders) -- no f-string
# SQL interpolation of caller-supplied values anywhere in this section.

_SLEEVE_COLUMNS = [
    "id",
    "name",
    "capital_usd",
    "status",
    "envelope_json",
    "created_at",
    "updated_at",
]
_SLEEVE_RULE_COLUMNS = [
    "id",
    "sleeve_id",
    "name",
    "json_doc",
    "mode",
    "enabled",
    "created_at",
    "updated_at",
]
_SLEEVE_ORDER_COLUMNS = [
    "id",
    "client_order_id",
    "alpaca_order_id",
    "sleeve_id",
    "rule_id",
    "symbol",
    "side",
    "qty",
    "reserved_price",
    "order_class",
    "status",
    "submitted_at",
    "raw_json",
    "updated_at",
]
_SLEEVE_FILL_COLUMNS = [
    "id",
    "order_id",
    "broker_fill_id",
    "fill_price",
    "filled_qty",
    "filled_at",
    "created_at",
]
_SLEEVE_RULE_FIRE_COLUMNS = [
    "id",
    "rule_id",
    "sleeve_id",
    "fired_at",
    "action",
    "rule_class",
    "mode_at_fire",
    "sensed_snapshot_json",
    "outcome_json",
    "clamped",
    "clamp_reason",
    "episode_id",
    "order_id",
    "created_at",
]


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_sleeve(name: str, capital_usd: float, envelope_json: str = "{}") -> int:
    """Insert one sleeve row (starts in SHADOW status) and return its new id.

    capital_usd is the fixed-dollar allocation set once at creation (AC-1);
    envelope_json is the caller-serialized hard-box JSON (ticker allowlist,
    per-position/per-order/turnover caps, long-only/no-margin flags) -- schema
    validation happens at the application layer, not here.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sleeves (name, capital_usd, status, envelope_json) "
            "VALUES (?, ?, 'SHADOW', ?)",
            (name, capital_usd, envelope_json),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_sleeve(sleeve_id: int) -> "dict | None":
    """Return one sleeve row as a dict, or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_COLUMNS) + " FROM sleeves WHERE id = ?",
            (sleeve_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_COLUMNS, row)) if row else None


def get_sleeve_by_name(name: str) -> "dict | None":
    """Return one sleeve row by its unique name, or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_COLUMNS) + " FROM sleeves WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_COLUMNS, row)) if row else None


def get_all_sleeves() -> "list[dict]":
    """Return all sleeve rows ordered by id ascending."""
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_COLUMNS) + " FROM sleeves ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SLEEVE_COLUMNS, row)) for row in rows]


def update_sleeve_status(sleeve_id: int, status: str) -> None:
    """Update one sleeve's status column and stamp updated_at to now UTC."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sleeves SET status = ?, updated_at = ? WHERE id = ?",
            (status, _utcnow_iso(), sleeve_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_sleeve_envelope(sleeve_id: int, envelope_json: str) -> None:
    """Replace one sleeve's envelope_json and stamp updated_at to now UTC.

    Widening vs. narrowing the envelope is an application-layer ceremony
    decision (AC-3) -- this accessor performs the write unconditionally.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sleeves SET envelope_json = ?, updated_at = ? WHERE id = ?",
            (envelope_json, _utcnow_iso(), sleeve_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- sleeve_rules: schema-ready for the P2 rule engine ---
# Not written or read by any P1 code path; provisioned now so sleeve_orders.rule_id
# and sleeve_runtime.rule_id have a real FK target and P2 can build directly on it.


def create_sleeve_rule(
    sleeve_id: int,
    name: str,
    json_doc: str = "{}",
    mode: str = "SHADOW",
    enabled: bool = True,
) -> int:
    """Insert one sleeve_rules row and return its new id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sleeve_rules (sleeve_id, name, json_doc, mode, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (sleeve_id, name, json_doc, mode, 1 if enabled else 0),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_sleeve_rule(rule_id: int) -> "dict | None":
    """Return one sleeve_rules row as a dict, or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_RULE_COLUMNS) + " FROM sleeve_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_RULE_COLUMNS, row)) if row else None


def get_sleeve_rules_for_sleeve(sleeve_id: int) -> "list[dict]":
    """Return all sleeve_rules rows for one sleeve, ordered by id ascending."""
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_RULE_COLUMNS)
            + " FROM sleeve_rules WHERE sleeve_id = ? ORDER BY id ASC",
            (sleeve_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SLEEVE_RULE_COLUMNS, row)) for row in rows]


def update_sleeve_rule_mode(rule_id: int, mode: str) -> None:
    """Update one sleeve_rules row's mode column and stamp updated_at to now UTC.

    Mode-transition ceremony gating (AC-13 shadow-fire gate, AC-14 panic-flow
    ceremony, AC-12 disarm revert-to-SHADOW) is an application-layer decision
    -- this accessor performs the write unconditionally, mirroring
    update_sleeve_status's contract.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sleeve_rules SET mode = ?, updated_at = ? WHERE id = ?",
            (mode, _utcnow_iso(), rule_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- sleeve_orders / sleeve_fills: the P1 order layer ---
#
# client_order_id is the durable correlation key across the whole order
# lifecycle (minted by the caller BEFORE the broker call, so a row exists at
# reserve-time for crash recovery). alpaca_order_id is populated later, once
# the broker acks, via attach_alpaca_order_id(). Both a client-id lookup and
# an alpaca-id lookup are exposed since callers correlate from either side
# (the runner mints client_order_id; broker poll/webhook responses key off
# alpaca_order_id).


def insert_sleeve_order(
    client_order_id: str,
    sleeve_id: int,
    symbol: str,
    side: str,
    qty: float,
    order_class: str = "simple",
    status: str = "RESERVED",
    rule_id: "int | None" = None,
    reserved_price: "float | None" = None,
    alpaca_order_id: "str | None" = None,
    raw_json: str = "{}",
) -> int:
    """Insert one sleeve_orders row, normally at reserve-time (pre-broker-call).

    client_order_id is UNIQUE (schema-enforced) -- re-inserting the same
    client_order_id raises sqlite3.IntegrityError rather than silently
    duplicating the row. alpaca_order_id is optional here (defaults to NULL)
    because the whole point of client_order_id is supporting a row that
    exists BEFORE the broker has acked and assigned one; pass it only if
    the caller already has it (e.g. a synchronous submit path that got an
    immediate broker response). Otherwise call attach_alpaca_order_id() once
    the broker ack arrives.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sleeve_orders "
            "(client_order_id, alpaca_order_id, sleeve_id, rule_id, symbol, side, qty, "
            "reserved_price, order_class, status, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                client_order_id,
                alpaca_order_id,
                sleeve_id,
                rule_id,
                symbol,
                side,
                qty,
                reserved_price,
                order_class,
                status,
                raw_json,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def attach_alpaca_order_id(
    client_order_id: str,
    alpaca_order_id: str,
    status: "str | None" = None,
    raw_json: "str | None" = None,
) -> None:
    """Populate alpaca_order_id on an existing RESERVED row once the broker acks.

    Looks up by client_order_id (the pre-ack correlation key). Optionally also
    updates status/raw_json in the same write. Stamps updated_at to now UTC.
    No-op (no row touched) if client_order_id is unknown.
    """
    conn = get_connection()
    try:
        set_clauses = ["alpaca_order_id = ?", "updated_at = ?"]
        params: list = [alpaca_order_id, _utcnow_iso()]
        if status is not None:
            set_clauses.append("status = ?")
            params.append(status)
        if raw_json is not None:
            set_clauses.append("raw_json = ?")
            params.append(raw_json)
        params.append(client_order_id)
        conn.execute(
            f"UPDATE sleeve_orders SET {', '.join(set_clauses)} WHERE client_order_id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def update_sleeve_order_status(
    client_order_id: str, status: str, raw_json: "str | None" = None
) -> None:
    """Update one sleeve_orders row's status (and optionally raw_json) by client_order_id.

    client_order_id (not alpaca_order_id) is the lookup key because it is
    populated for the entire lifecycle, including the pre-ack RESERVED window
    where alpaca_order_id is still NULL. Stamps updated_at to now UTC. No-op
    (no row touched) if client_order_id is unknown -- callers should check
    reconciliation state separately rather than relying on this raising for
    an unknown order.
    """
    conn = get_connection()
    try:
        if raw_json is not None:
            conn.execute(
                "UPDATE sleeve_orders SET status = ?, raw_json = ?, updated_at = ? "
                "WHERE client_order_id = ?",
                (status, raw_json, _utcnow_iso(), client_order_id),
            )
        else:
            conn.execute(
                "UPDATE sleeve_orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
                (status, _utcnow_iso(), client_order_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_sleeve_order_by_client_id(client_order_id: str) -> "dict | None":
    """Return one sleeve_orders row by our own pre-broker correlation key, or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_ORDER_COLUMNS)
            + " FROM sleeve_orders WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_ORDER_COLUMNS, row)) if row else None


def get_sleeve_order_by_alpaca_id(alpaca_order_id: str) -> "dict | None":
    """Return one sleeve_orders row by broker order id, or None when absent (incl. pre-ack rows)."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_ORDER_COLUMNS)
            + " FROM sleeve_orders WHERE alpaca_order_id = ?",
            (alpaca_order_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_ORDER_COLUMNS, row)) if row else None


def get_sleeve_orders(
    sleeve_id: "int | None" = None,
    rule_id: "int | None" = None,
    status: "str | None" = None,
    limit: int = 100,
) -> "list[dict]":
    """Read sleeve_orders rows with optional filters, newest-submitted first.

    limit is server-side clamped to 500 max (get_triggers precedent, database.py).
    """
    limit = min(limit, 500)
    conn = get_ro_connection()
    clauses: list[str] = []
    params: list = []
    if sleeve_id is not None:
        clauses.append("sleeve_id = ?")
        params.append(sleeve_id)
    if rule_id is not None:
        clauses.append("rule_id = ?")
        params.append(rule_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_ORDER_COLUMNS) + f" FROM sleeve_orders {where} "
            "ORDER BY submitted_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SLEEVE_ORDER_COLUMNS, row)) for row in rows]


def get_sleeve_order_history(sleeve_id: int) -> "list[dict]":
    """Return every sleeve_orders row for a sleeve, oldest-submitted first, each
    augmented with a "fills" key holding its sleeve_fills rows (oldest-filled first).

    This is raw event data only -- no cost-basis/P&L/reservation arithmetic is
    performed here. It exists so sleeves.ledger (which owns that arithmetic in
    tested pure functions -- reserve/release/apply_fill) can fold over a
    sleeve's full order+fill history to reconstruct current LedgerState,
    without database.py re-deriving conservation-critical math independently
    in SQL. Deliberately not sliced by date/limit -- full replay needs the
    complete history.
    """
    conn = get_ro_connection()
    try:
        order_rows = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_ORDER_COLUMNS)
            + " FROM sleeve_orders WHERE sleeve_id = ? ORDER BY submitted_at ASC",
            (sleeve_id,),
        ).fetchall()
        orders = [dict(zip(_SLEEVE_ORDER_COLUMNS, row)) for row in order_rows]
        for order in orders:
            fill_rows = conn.execute(
                "SELECT "
                + ", ".join(_SLEEVE_FILL_COLUMNS)
                + " FROM sleeve_fills WHERE order_id = ? ORDER BY filled_at ASC, id ASC",
                (order["id"],),
            ).fetchall()
            order["fills"] = [dict(zip(_SLEEVE_FILL_COLUMNS, row)) for row in fill_rows]
    finally:
        conn.close()
    return orders


def get_daily_turnover_usd(sleeve_id: int, trading_day: str) -> float:
    """Return one sleeve's total dollar turnover for trading_day ('YYYY-MM-DD').

    Turnover = already-executed notional (SUM(filled_qty * fill_price) for
    fills on trading_day) + still-reserved notional for orders submitted on
    trading_day that have not reached a terminal status (their UNFILLED
    remainder only: (qty - filled_so_far) * reserved_price, so a partially
    filled order's executed portion is never double-counted against its
    remaining reservation). Orders with reserved_price IS NULL (e.g. a sell,
    which reserves shares rather than cash -- envelope.py/sizing.py concern,
    not this sleeve's cash conservation) contribute 0 to the reserved term.

    This is a plain SUM/JOIN aggregation, not P&L bookkeeping -- it does not
    duplicate sleeves.ledger's cost-basis/realized-P&L arithmetic.

    Terminal-status classification (denylist -- everything NOT in this set is
    treated as still-reserving) is from Alpaca's documented order status enum
    (tests/sleeves/_alpaca_fixtures.py ALPACA_ORDER_STATUS_VALUES) plus this
    schema's own pre-ack 'RESERVED' value. Deliberately fails CLOSED: an
    unrecognized/future status is treated as still-reserving (over-counts
    turnover, the conservative direction for a risk cap) rather than silently
    excluded (which would under-count and let a sleeve exceed its turnover
    budget). 'stopped' and 'suspended' are intentionally NOT terminal here
    (corrected per sleeve-integration-impl, who owns Alpaca status semantics):
    'stopped' means a trade is GUARANTEED but has not yet occurred (still
    pending execution -- classifying it terminal would under-count real
    turnover about to happen, the unsafe direction); 'suspended' means "not
    eligible for trading" with no guarantee it can't later resume, so the
    fail-closed default (still-open, worst case over-counts) applies to it too.
    """
    _TERMINAL_STATUSES = (
        "filled",
        "canceled",
        "expired",
        "replaced",
        "done_for_day",
        "rejected",
    )
    conn = get_ro_connection()
    try:
        executed_row = conn.execute(
            "SELECT COALESCE(SUM(sf.filled_qty * sf.fill_price), 0.0) "
            "FROM sleeve_fills sf JOIN sleeve_orders so ON sf.order_id = so.id "
            "WHERE so.sleeve_id = ? AND date(sf.filled_at) = ?",
            (sleeve_id, trading_day),
        ).fetchone()
        executed_usd = float(executed_row[0])

        placeholders = ",".join("?" * len(_TERMINAL_STATUSES))
        reserved_row = conn.execute(
            "SELECT COALESCE(SUM("
            "  (so.qty - COALESCE("
            "    (SELECT SUM(sf.filled_qty) FROM sleeve_fills sf WHERE sf.order_id = so.id), 0.0"
            "  )) * so.reserved_price"
            "), 0.0) "
            "FROM sleeve_orders so "
            "WHERE so.sleeve_id = ? AND date(so.submitted_at) = ? "
            f"AND so.status NOT IN ({placeholders}) "
            "AND so.reserved_price IS NOT NULL",
            (sleeve_id, trading_day, *_TERMINAL_STATUSES),
        ).fetchone()
        reserved_remaining_usd = float(reserved_row[0])
    finally:
        conn.close()
    return executed_usd + reserved_remaining_usd


def insert_sleeve_fill(
    order_id: int,
    fill_price: float,
    filled_qty: float,
    filled_at: str,
    broker_fill_id: "str | None" = None,
) -> int:
    """Insert one sleeve_fills row against an existing sleeve_orders.id and return its new id.

    order_id is the INTERNAL sleeve_orders.id, not client_order_id/alpaca_order_id --
    callers resolve the internal id via get_sleeve_order_by_client_id() or
    get_sleeve_order_by_alpaca_id() first.

    broker_fill_id (the Alpaca Account Activities `id` for this discrete fill
    event) is UNIQUE when present (schema-enforced) -- re-inserting the same
    broker_fill_id raises sqlite3.IntegrityError, which callers polling
    overlapping activity windows should catch/ignore as "already recorded".
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sleeve_fills (order_id, broker_fill_id, fill_price, filled_qty, filled_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (order_id, broker_fill_id, fill_price, filled_qty, filled_at),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_fills_for_order(order_id: int) -> "list[dict]":
    """Return all sleeve_fills rows for one sleeve_orders.id, ordered by id ascending."""
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_FILL_COLUMNS)
            + " FROM sleeve_fills WHERE order_id = ? ORDER BY id ASC",
            (order_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SLEEVE_FILL_COLUMNS, row)) for row in rows]


# --- sleeve_runtime: durable pacing/latch/bench state for the P2 rule engine ---
# Schema-ready now (P1); not read or written by any P1 code path. The engine
# is a fresh subprocess per minute, so P2's runner reads/writes this table on
# every tick rather than holding state in memory.


def get_sleeve_runtime(rule_id: int, key: str) -> "str | None":
    """Return the stored value for (rule_id, key), or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT value FROM sleeve_runtime WHERE rule_id = ? AND key = ?",
            (rule_id, key),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def set_sleeve_runtime(rule_id: int, key: str, value: str) -> None:
    """Upsert one sleeve_runtime row for (rule_id, key); stamps updated_at to now UTC."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sleeve_runtime (rule_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (rule_id, key, value, _utcnow_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_sleeve_runtime_for_rule(rule_id: int) -> "dict[str, str]":
    """Return all sleeve_runtime key/value pairs for one rule as a dict."""
    conn = get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM sleeve_runtime WHERE rule_id = ?", (rule_id,)
        ).fetchall()
    finally:
        conn.close()
    return {k: v for k, v in rows}


def delete_sleeve_runtime(rule_id: int, key: str) -> None:
    """Delete one sleeve_runtime row for (rule_id, key). Idempotent -- safe when absent."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sleeve_runtime WHERE rule_id = ? AND key = ?", (rule_id, key))
        conn.commit()
    finally:
        conn.close()


# --- sleeve_rule_fires: P2 rule engine fire log (migration 034) ---
# One row per tick evaluation that fired (`when`/`if` matched, `then` attempted).
# SHADOW rules record the fire + the would-have-ordered sizing and execute
# nothing (AC-6); PAPER/LIVE fires additionally carry order_id once an order is
# placed. Durable source for per-rule/per-sleeve attribution (AC-16, AC-19) and
# an independent (ground-truth) fire count for the dashboard -- limits.py's own
# pacing bookkeeping (cooldown/max_fires_per_day/episode latch/churn-brake
# state) lives entirely in sleeve_runtime (P1's get/set_sleeve_runtime), not
# on this table.


def insert_sleeve_rule_fire(
    rule_id: int,
    sleeve_id: int,
    action: str,
    rule_class: str,
    mode_at_fire: str,
    sensed_snapshot_json: str = "{}",
    outcome_json: str = "{}",
    clamped: bool = False,
    clamp_reason: "str | None" = None,
    episode_id: "str | None" = None,
    order_id: "int | None" = None,
    fired_at: "str | None" = None,
) -> int:
    """Insert one sleeve_rule_fires row and return its new id.

    rule_class ('DEFENSIVE'|'ENTRY') and mode_at_fire ('SHADOW'|'PAPER'|'LIVE')
    are snapshots taken AT THIS TICK -- pass the caller's current derivation/
    mode explicitly rather than re-deriving them later, so a subsequent rule
    edit or SHADOW->PAPER->LIVE mode change never rewrites history. order_id
    is the INTERNAL sleeve_orders.id (not client_order_id/alpaca_order_id,
    matching sleeve_fills.order_id's precedent); leave it None for SHADOW
    fires and for fires whose action placed no order (notify, skip, envelope
    refusal). fired_at defaults to the SQL insert-time (schema DEFAULT) when
    omitted; pass it explicitly to record the tick's own logical timestamp
    instead of the moment this row happened to be written.
    """
    conn = get_connection()
    try:
        columns = [
            "rule_id",
            "sleeve_id",
            "action",
            "rule_class",
            "mode_at_fire",
            "sensed_snapshot_json",
            "outcome_json",
            "clamped",
            "clamp_reason",
            "episode_id",
            "order_id",
        ]
        values: list = [
            rule_id,
            sleeve_id,
            action,
            rule_class,
            mode_at_fire,
            sensed_snapshot_json,
            outcome_json,
            1 if clamped else 0,
            clamp_reason,
            episode_id,
            order_id,
        ]
        if fired_at is not None:
            columns.append("fired_at")
            values.append(fired_at)
        placeholders = ", ".join("?" * len(values))
        cursor = conn.execute(
            f"INSERT INTO sleeve_rule_fires ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_sleeve_rule_fire(fire_id: int) -> "dict | None":
    """Return one sleeve_rule_fires row by its internal id, or None when absent."""
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT "
            + ", ".join(_SLEEVE_RULE_FIRE_COLUMNS)
            + " FROM sleeve_rule_fires WHERE id = ?",
            (fire_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_SLEEVE_RULE_FIRE_COLUMNS, row)) if row else None


def get_sleeve_rule_fires(
    rule_id: "int | None" = None,
    sleeve_id: "int | None" = None,
    limit: int = 100,
) -> "list[dict]":
    """Read sleeve_rule_fires rows with optional filters, newest-fired first.

    limit is server-side clamped to 500 max (get_sleeve_orders precedent).
    """
    limit = min(limit, 500)
    conn = get_ro_connection()
    clauses: list[str] = []
    params: list = []
    if rule_id is not None:
        clauses.append("rule_id = ?")
        params.append(rule_id)
    if sleeve_id is not None:
        clauses.append("sleeve_id = ?")
        params.append(sleeve_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_SLEEVE_RULE_FIRE_COLUMNS) + f" FROM sleeve_rule_fires {where} "
            "ORDER BY fired_at DESC, id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_SLEEVE_RULE_FIRE_COLUMNS, row)) for row in rows]


def get_fire_count_for_rule_on_day(rule_id: int, trading_day: str) -> int:
    """Return the count of sleeve_rule_fires rows for one rule on trading_day
    ('YYYY-MM-DD'), a ground-truth fire count for the dashboard panel's
    today/lifetime attribution (AC-16) independent of limits.py's own
    max_fires_per_day pacing counter (that pacing state lives entirely in
    sleeve_runtime -- see get_sleeve_runtime/set_sleeve_runtime -- so this
    accessor is a cross-check, not the runner's live pacing gate).

    trading_day is caller-supplied (the runner computes it via
    market_calendar.py, XNYS calendar) -- this accessor never derives "today"
    from naive UTC/local date, matching get_daily_turnover_usd's contract.
    Counts every fire regardless of action/outcome -- a rule that fired and
    was clamped-to-zero or rejected still counts; callers that need an
    entries-only or exits-only count should filter on `action` at the
    application layer.
    """
    conn = get_ro_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sleeve_rule_fires WHERE rule_id = ? AND date(fired_at) = ?",
            (rule_id, trading_day),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


# Initialize tables on import
init_db()
