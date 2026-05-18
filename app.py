"""Flask application for AlphaBot Control Center with Account-Level settings."""

import atexit
import os
import signal
import sys
import io
import time
import threading
import subprocess
from datetime import datetime
import schedule
import requests
import logging
import psutil
from flask import Flask, render_template, jsonify, request
from dotenv import dotenv_values, set_key

import database
import analytics
import ai_advisor
from market_calendar import get_market_state
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

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
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alphabot_daemon.log")

app = Flask(__name__)
# Reload .html templates on every request without restarting the process.
# NOTE: use_reloader / debug auto-restart are intentionally NOT enabled — the
# process owns a minute-scheduler that spawns real-money execution subprocesses;
# a Python-code restart would interrupt live ops.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

_daemon_log = logging.getLogger("alphabot")
_daemon_log.setLevel(logging.DEBUG)
_daemon_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_daemon_fh.setLevel(logging.DEBUG)
_daemon_log.addHandler(_daemon_fh)

COMPOSER_BASE_URL = "https://api.composer.trade/api/v0.1"

# Recorded at import time — used by /api/state daemon_started_at field (AC-P2.12.2)
# and the sticky restart-notice comparison (AC-P2.2.4 BC H7).
_DAEMON_STARTED_AT: str = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Daemon singleton — pidfile lifecycle
# ---------------------------------------------------------------------------
# Resolves the pidfile path at import time so both startup and shutdown always
# reference the same absolute path, regardless of cwd changes.
_PIDFILE_PATH: str = os.environ.get(
    "ALPHABOT_PIDFILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "alphabot.pid"),
)


def _is_alphabot_process_alive(pid: int) -> bool:
    """Return True if *pid* is a live python process whose argv contains 'app.py'.

    Uses psutil for a single-call, cross-platform check.  Returns False for
    any psutil exception (NoSuchProcess, AccessDenied, ZombieProcess) so that
    a stale pidfile is always treated as dead rather than blocking a restart.
    """
    try:
        proc = psutil.Process(pid)
        # cmdline() raises NoSuchProcess / AccessDenied if the process is gone
        # or belongs to another user; both are treated as "not alive".
        cmdline = proc.cmdline()
        return any("app.py" in arg for arg in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _write_pidfile(path: str, pid: int) -> None:
    """Atomically write *pid* to *path* (plain text, no trailing newline)."""
    with open(path, "w", encoding="ascii") as fh:
        fh.write(str(pid))


def _read_pidfile(path: str) -> int | None:
    """Read the integer PID from *path*.  Returns None on any read/parse error."""
    try:
        with open(path, "r", encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _remove_pidfile_if_ours(path: str, our_pid: int) -> None:
    """Remove *path* only if it still contains *our_pid*.

    Called at exit — never clobbers a pidfile written by a different daemon
    instance (e.g., if this process was the stale one and a new daemon already
    took ownership).
    """
    stored = _read_pidfile(path)
    if stored == our_pid:
        try:
            os.remove(path)
        except OSError:
            pass  # Already gone — fine.


def _acquire_daemon_singleton(pidfile: str) -> None:
    """Enforce the daemon singleton contract at startup.

    Reads the pidfile (if present) and checks whether the stored PID refers to
    a live AlphaBot process.

      - Live process found  → print error and exit(1).  Flask and the scheduler
        are never started.
      - Dead / stale PID    → log a notice, take ownership, continue.
      - No pidfile          → create it, continue.

    Registers an atexit handler and a SIGTERM handler to remove the pidfile on
    clean shutdown.  On Windows, SIGTERM may not be delivered reliably by
    Stop-Process; the atexit handler covers the normal Ctrl+C / graceful-exit
    path.
    """
    our_pid = os.getpid()

    if os.path.exists(pidfile):
        stored_pid = _read_pidfile(pidfile)
        if stored_pid is not None and _is_alphabot_process_alive(stored_pid):
            print(
                f"Another AlphaBot daemon is already running (PID {stored_pid}); "
                f"refusing to start. "
                f"If this is wrong, delete {pidfile} or stop PID {stored_pid} first.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Stale pidfile — the stored process is dead.
        print(
            f"Stale pidfile found (PID {stored_pid} not alive); taking ownership.",
            flush=True,
        )

    _write_pidfile(pidfile, our_pid)

    # --- Cleanup handlers ---
    # atexit covers: clean Ctrl+C, normal Python exit, app.run() returning.
    atexit.register(_remove_pidfile_if_ours, pidfile, our_pid)

    # SIGTERM: sent by restart.ps1 → Stop-Process.  On Windows the signal
    # module supports SIGTERM as of Python 3.8+, but CPython converts it to a
    # KeyboardInterrupt rather than running the signal handler directly.  We
    # register it anyway so the cleanup runs on POSIX-compatible environments
    # (WSL, CI).  The atexit handler is the reliable path on native Windows.
    def _sigterm_handler(signum, frame):  # noqa: ANN001
        _remove_pidfile_if_ours(pidfile, our_pid)
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (OSError, ValueError):
        # ValueError: signal only works in the main thread.
        # OSError: SIGTERM not available on this platform (shouldn't happen).
        pass


# --- 1. Bot Execution Logic ---
def trigger_alpha_bot(force=False):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Triggering Alpha Bot...")
    try:
        cmd = [sys.executable, "alpha_bot_execution.py"]
        if force:
            cmd.append("--force")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        with open(LOG_FILE, "a", encoding="utf-8") as log_fh:
            subprocess.run(cmd, check=True, env=env, stdout=log_fh, stderr=log_fh)
    except subprocess.CalledProcessError as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Execution failed: {e}")

def threaded_trigger():
    threading.Thread(target=trigger_alpha_bot, daemon=True).start()

def _run_trigger_retention():
    from dotenv import dotenv_values
    env_vars = dotenv_values(ENV_FILE_PATH)
    retention_days = int(env_vars.get("TRIGGER_TELEMETRY_RETENTION_DAYS", "90"))
    deleted = database.prune_old_triggers(retention_days)
    if deleted:
        print(f"[retention] pruned {deleted} old exit_triggers rows (>{retention_days}d)")
    # BC-4: prune shadow_history alongside exit_triggers in the same scheduler callback.
    shadow_retention_days = int(env_vars.get("SHADOW_HISTORY_RETENTION_DAYS", "180"))
    shadow_deleted = database.prune_old_shadow_history(shadow_retention_days)
    if shadow_deleted:
        print(f"[retention] pruned {shadow_deleted} old shadow_history rows (>{shadow_retention_days}d)")

def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    schedule.every().day.at("02:00").do(_run_trigger_retention)
    while True:
        schedule.run_pending()
        time.sleep(1)

# --- 2. Web Dashboard Routes ---
@app.route("/")
def dashboard():
    vars_locked_count = 0
    state = database.load_state()
    for sym_id, sym_data in state.items():
        if sym_id == "date":
            continue
        name = database.normalize_name(sym_data.get("name", ""))
        strategy = database.get_symphony_strategy(name)
        if strategy:
            vars_locked_count += len(strategy.get("locked_vars") or [])
    return render_template("index.html", vars_locked_count=vars_locked_count)


def get_api_state_dict() -> dict:
    """
    Return the core state dict consumed by /api/state and testable without HTTP.

    Additive fields (AC-P2.12.2): port_state, exit_authority, daemon_started_at.
    No existing field is renamed or removed.
    """
    from engine.exit_authority import get_exit_authority
    bot_state = database.load_state()
    exit_authority = get_exit_authority()

    # Read lock status directly — no dedicated helper exists for read-only lock query
    _ro = None
    try:
        _ro = database.get_ro_connection()
        _cur = _ro.execute("SELECT is_locked FROM execution_lock WHERE id = 1")
        _row = _cur.fetchone()
        is_locked = bool(_row[0]) if _row else False
    except Exception:
        is_locked = False
    finally:
        if _ro is not None:
            try:
                _ro.close()
            except Exception:
                pass

    port_state: dict = {}
    if hasattr(database, "read_port_state"):
        # sqlite-specialist migration 010 may not have landed in all environments yet
        try:
            accounts = {
                v.get("account")
                for v in bot_state.values()
                if isinstance(v, dict) and v.get("account")
            }
            for acc_id in accounts:
                row = database.read_port_state(acc_id)
                if row is not None:
                    port_state[acc_id] = row
        except Exception:
            pass

    return {
        "bot_state": bot_state,
        "is_locked": is_locked,
        "port_state": port_state,
        "exit_authority": exit_authority,
        "daemon_started_at": _DAEMON_STARTED_AT,
    }


@app.route("/api/state")
def get_state():
    try:
        _ro_conn = database.get_ro_connection()
        market_state = get_market_state(datetime.now(_ET))

        # AC-P2.12.2: additive fields — computed once, merged into every response branch.
        _api_state = get_api_state_dict()
        _additive = {
            "port_state": _api_state["port_state"],
            "exit_authority": _api_state["exit_authority"],
            "daemon_started_at": _DAEMON_STARTED_AT,
        }

        state_data = database.load_state()

        # AC-DM.3.3: closed + snapshot → serve frozen snapshot.
        if market_state in ("closed_frozen", "pre_market"):
            snapshot = (state_data or {}).get("last_market_close_snapshot")
            if snapshot:
                # R2: remap shadow_divergence "portfolio" -> "portfolio_today" to match
                # the live path's key name (from database.get_shadow_divergence()).
                sd = dict(snapshot.get("shadow_divergence") or {})
                if "portfolio" in sd and "portfolio_today" not in sd:
                    sd["portfolio_today"] = sd.pop("portfolio")
                _alert_row = database.read_fleet_alert()
                _alert = _alert_row if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None) else None
                _state: dict = {}
                for _acc_entries in (snapshot.get("accounts_map") or {}).values():
                    for _sym in (_acc_entries or []):
                        if isinstance(_sym, dict) and "id" in _sym:
                            _state[_sym["id"]] = _sym
                sd_by_sym = sd.get("by_symphony") or {}
                for sym_id, entry in sd_by_sym.items():
                    if isinstance(entry, dict) and "name" not in entry:
                        entry = dict(entry)
                        sd_by_sym[sym_id] = entry
                        entry["name"] = (_state.get(sym_id) or {}).get("name") or sym_id

                # Build accounts_map + account_labels for table rendering.
                # Reuse snapshot.accounts_map directly (already per-account lists).
                _snap_accounts_map = snapshot.get("accounts_map") or {}

                # Sorting: honour sortCol / sortDir query params, same as live branch.
                _sort_col = request.args.get("sortCol", "name")
                _sort_dir = request.args.get("sortDir", "asc")
                _is_desc = (_sort_dir == "desc")

                def _frozen_status_rank(s: dict) -> int:
                    if s.get("triggered"):
                        if s.get("triggered_reason") == "VWAP Breakdown":
                            return 5
                        return 4
                    if s.get("para_armed"):
                        return 3
                    if s.get("tp_armed"):
                        return 2
                    if s.get("armed"):
                        return 1
                    return 0

                def _frozen_exit_ret(s: dict) -> float:
                    if s.get("triggered"):
                        r = s.get("triggered_at_return")
                        return r if r is not None else (s.get("current_return") or -999.0)
                    return s.get("current_return") if s.get("current_return") is not None else -999.0

                for _acc_id in _snap_accounts_map:
                    _syms = _snap_accounts_map[_acc_id]
                    if _sort_col == "mc_prob":
                        _syms.sort(key=lambda s: s.get("mc_prob") if s.get("mc_prob") is not None else -999.0, reverse=_is_desc)
                    elif _sort_col == "status":
                        _syms.sort(key=_frozen_status_rank, reverse=_is_desc)
                    elif _sort_col == "stop_level":
                        _syms.sort(key=lambda s: s.get("triggered_at_stop") if s.get("triggered") and s.get("triggered_at_stop") is not None else (s.get("stop_trigger") if s.get("stop_trigger") is not None else -999.0), reverse=_is_desc)
                    elif _sort_col == "current_return":
                        _syms.sort(key=_frozen_exit_ret, reverse=_is_desc)
                    elif _sort_col == "shadow_hwm":
                        _syms.sort(key=lambda s: s.get("shadow_hwm", -999.0), reverse=_is_desc)
                    elif _sort_col == "shadow":
                        _syms.sort(key=lambda s: s.get("current_return") if s.get("current_return") is not None else -999.0, reverse=_is_desc)
                    else:  # name (default)
                        _syms.sort(key=lambda s: (s.get("name") or s.get("id", "")).lower(), reverse=_is_desc)

                # Enrich each snapshot symphony with TC/CR/MDD analytics, using
                # snapshot["trading_day"] so analytics reads from shadow_history for
                # that day (R14 contract — NOT today's date).
                _snap_trading_day = snapshot.get("trading_day")
                for _acc_syms in _snap_accounts_map.values():
                    for _sym in (_acc_syms or []):
                        if not isinstance(_sym, dict):
                            continue
                        _sym_id = _sym.get("id", "")
                        _cr_val = _sym.get("current_return") or 0.0
                        _val = _sym.get("current_value") or 0.0
                        _sym_dict = {
                            "id": _sym_id,
                            "value": _val,
                            "last_percent_change": _cr_val / 100.0,
                            "simple_return": _sym.get("simple_return"),
                            "net_deposits": _sym.get("net_deposits"),
                            "time_weighted_return": _sym.get("time_weighted_return"),
                            "max_drawdown": _sym.get("max_drawdown"),
                            "trading_day": _snap_trading_day,
                        }
                        try:
                            _sym["_tc"] = analytics.get_symphony_today_change(_sym_dict, _sym, trading_day=_snap_trading_day)
                        except (KeyError, TypeError, ValueError):
                            _sym["_tc"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_cr"] = analytics.get_symphony_cumulative_return(_sym_dict, _sym, trading_day=_snap_trading_day)
                        except (KeyError, TypeError, ValueError):
                            _sym["_cr"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_mdd"] = analytics.get_symphony_max_drawdown(_sym_dict, _sym, trading_day=_snap_trading_day)
                        except (KeyError, TypeError, ValueError):
                            _sym["_mdd"] = {"if_held": None, "dry_run": None}

                # Account labels: read from env (same as live branch; scrub raw IDs from UI).
                _env_vars_frozen = dotenv_values(ENV_FILE_PATH)
                _account_labels_frozen: dict = {}
                _acc_ind = _env_vars_frozen.get("ACCOUNT_INDIVIDUAL", "").strip()
                _acc_roth = _env_vars_frozen.get("ACCOUNT_ROTH", "").strip()
                _acc_trad = _env_vars_frozen.get("ACCOUNT_TRAD", "").strip()
                if _acc_ind:
                    _account_labels_frozen[_acc_ind] = "Individual"
                if _acc_roth:
                    _account_labels_frozen[_acc_roth] = "Roth IRA"
                if _acc_trad:
                    _account_labels_frozen[_acc_trad] = "Trad. IRA"

                # Normalise snapshot symphony dicts to ensure the template's
                # numeric filters (e.g. "%.1f"|format, |round) receive float or
                # None, never Jinja Undefined (which would bypass the `is not none`
                # guard and raise a format error on old/minimal snapshots).
                _FROZEN_SYM_DEFAULTS: dict = {
                    "mc_prob": None,
                    "shadow_hwm": 0.0,
                    "stop_trigger": None,
                    "current_return": 0.0,
                    "current_value": 0.0,
                    "breakeven_locked": False,
                    "armed": False,
                    "tp_armed": False,
                    "para_armed": False,
                    "triggered": False,
                    "above_tp_count": 0,
                    "below_stop_count": 0,
                    "last_trigger": None,
                }
                for _acc_syms_n in _snap_accounts_map.values():
                    for _sym_n in (_acc_syms_n or []):
                        if isinstance(_sym_n, dict):
                            for _field, _default in _FROZEN_SYM_DEFAULTS.items():
                                _sym_n.setdefault(_field, _default)

                # On-the-fly portfolio_strip recompute — authoritative, never pass-through.
                # Recompute from accounts_map so stale/None captured values are never surfaced.
                # R14: use snapshot.trading_day, not today's date.
                _snap_symphonies_list = []
                _snap_bot_state = {}
                for _acc_syms_r in _snap_accounts_map.values():
                    for _sym_r in (_acc_syms_r or []):
                        if not isinstance(_sym_r, dict):
                            continue
                        _sid_r = _sym_r.get("id", "")
                        _cr_r = _sym_r.get("current_return") or 0.0
                        _val_r = _sym_r.get("current_value") or 0.0
                        _snap_symphonies_list.append({
                            "id": _sid_r,
                            "value": _val_r,
                            "last_percent_change": _cr_r / 100.0,
                            "simple_return": _sym_r.get("simple_return"),
                            "net_deposits": _sym_r.get("net_deposits"),
                            "time_weighted_return": _sym_r.get("time_weighted_return"),
                            "max_drawdown": _sym_r.get("max_drawdown"),
                            "trading_day": _snap_trading_day,
                        })
                        _snap_bot_state[_sid_r] = {
                            "current_value": _val_r,
                            "current_return": _cr_r,
                            "name": _sym_r.get("name"),
                            "account": _sym_r.get("account"),
                        }
                try:
                    _portfolio_strip = {
                        "today_change": analytics.get_portfolio_today_change(
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                        ),
                        "cumulative_return": analytics.get_portfolio_cumulative_return(
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                        ),
                        "max_drawdown": analytics.get_portfolio_max_drawdown(
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                        ),
                    }
                except Exception:
                    _portfolio_strip = {"today_change": None, "cumulative_return": None, "max_drawdown": None}

                try:
                    _frozen_html = render_template(
                        "table_partial.html",
                        accounts_map=_snap_accounts_map,
                        account_labels=_account_labels_frozen,
                        sort_col=_sort_col,
                        sort_dir=_sort_dir,
                        data_as_of=snapshot.get("data_as_of"),
                    )
                except Exception:
                    _frozen_html = "<table><tbody></tbody></table>"

                return jsonify({
                    "status": "active",
                    "market_state": market_state,
                    "frozen_at": snapshot.get("captured_at_et"),
                    "data_as_of": snapshot.get("data_as_of"),
                    "state": _state,
                    "portfolio_strip": _portfolio_strip,
                    "shadow_divergence": sd,
                    "accounts_map": snapshot.get("accounts_map"),
                    "fleet_correlation_alert": _alert,
                    "html": _frozen_html,
                    **_additive,
                })

        # No live state — return waiting with market_state context and notice on fresh deploy.
        # AC-DM.3.4: closed + no snapshot + empty state → notice fields included in waiting.
        if not state_data:
            today_str = datetime.now().strftime("%Y-%m-%d")
            try:
                shadow_divergence = database.get_shadow_divergence(today_str)
            except Exception:
                shadow_divergence = {"by_symphony": {}, "portfolio_today": None}
            for sym_id, entry in shadow_divergence["by_symphony"].items():
                entry["name"] = sym_id
            _alert_row = database.read_fleet_alert()
            _alert = _alert_row if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None) else None
            waiting_resp = {
                "status": "waiting",
                "message": "Bot state initializing.",
                "shadow_divergence": shadow_divergence,
                "market_state": market_state,
                "frozen_at": None,
                "state": {},
                "fleet_correlation_alert": _alert,
            }
            # AC-DM.3.4: include notice on fresh deploy (no snapshot, market closed)
            if market_state in ("closed_frozen", "pre_market"):
                waiting_resp["notice"] = "No closing snapshot yet — waiting for first market close at 16:00 ET."
            return jsonify({**waiting_resp, **_additive})

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
        # Composer CR/MDD fields use None default so missing data is distinguishable from 0.0.
        today_str = datetime.now().strftime("%Y-%m-%d")
        symphonies_list = []
        for k in symphony_keys:
            s = state_data[k]
            cr = s.get("current_return") or 0.0
            val = s.get("current_value") or 0.0
            symphonies_list.append({
                "id": k,
                "value": val,
                "last_percent_change": cr / 100.0,
                "simple_return": s.get("simple_return"),
                "net_deposits": s.get("net_deposits"),
                "time_weighted_return": s.get("time_weighted_return"),
                "max_drawdown": s.get("max_drawdown"),
                "trading_day": today_str,
            })

        # Attach last_trigger (today's most-recent) to each symphony for the Status sub-line.
        today_start = datetime.now().strftime("%Y-%m-%d") + "T00:00:00Z"
        try:
            today_triggers = database.get_triggers(since=today_start, limit=500)
            last_trigger_by_sym = {}
            for t in today_triggers:
                sid = t["symphony_id"]
                if sid not in last_trigger_by_sym:
                    last_trigger_by_sym[sid] = t
        except Exception:
            last_trigger_by_sym = {}
        for k in symphony_keys:
            state_data[k]["last_trigger"] = last_trigger_by_sym.get(k)

        # Attach per-symphony TC/CR/MDD to each sym dict so the template can render them.
        _today_et = datetime.now(_ET).strftime("%Y-%m-%d")
        for k in symphony_keys:
            s = state_data[k]
            sym_dict = next((d for d in symphonies_list if d["id"] == k), {})
            try:
                s["_tc"] = analytics.get_symphony_today_change(sym_dict, s, trading_day=_today_et)
            except (KeyError, TypeError, ValueError):
                s["_tc"] = {"if_held": None, "dry_run": None}
            try:
                s["_cr"] = analytics.get_symphony_cumulative_return(sym_dict, s, trading_day=_today_et)
            except (KeyError, TypeError, ValueError):
                s["_cr"] = {"if_held": None, "dry_run": None}
            try:
                s["_mdd"] = analytics.get_symphony_max_drawdown(sym_dict, s, trading_day=_today_et)
            except (KeyError, TypeError, ValueError):
                s["_mdd"] = {"if_held": None, "dry_run": None}

        portfolio_strip = {
            "today_change": analytics.get_portfolio_today_change(
                symphonies_list, state_data, trading_day=_today_et
            ),
            "cumulative_return": analytics.get_portfolio_cumulative_return(
                symphonies_list, state_data, trading_day=_today_et
            ),
            "max_drawdown": analytics.get_portfolio_max_drawdown(
                symphonies_list, state_data, trading_day=_today_et
            ),
        }

        data_as_of = datetime.now().strftime("%H:%M ET")

        rendered_html = render_template("table_partial.html", accounts_map=accounts_map, account_labels=account_labels, sort_col=sort_col, sort_dir=sort_dir, data_as_of=data_as_of)

        # PA-M1F-14: shadow_divergence — one lightweight GROUP BY query, not on execution path.
        try:
            shadow_divergence = database.get_shadow_divergence(today_str)
        except Exception:
            shadow_divergence = {"by_symphony": {}, "portfolio_today": None}
        for sym_id, entry in shadow_divergence["by_symphony"].items():
            entry["name"] = (state_data.get(sym_id) or {}).get("name") or sym_id

        _alert_row = database.read_fleet_alert()
        _alert = _alert_row if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None) else None

        return jsonify({
            "status": "active",
            "market_state": market_state,
            "frozen_at": None,
            "state": state_data,
            "live_mode": live_mode,
            "execution_start_time": env_vars.get("EXECUTION_START_TIME", "09:30"),
            "next_run_seconds": next_run_seconds,
            "html": rendered_html,
            "portfolio_strip": portfolio_strip,
            "data_as_of": data_as_of,
            "last_successful_cycle_at": state_data.get("last_successful_cycle_at"),
            "shadow_divergence": shadow_divergence,
            "fleet_correlation_alert": _alert,
            **_additive,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            _ro_conn.close()
        except Exception:
            pass

@app.route("/api/logs/<symphony_id>")
def api_symphony_logs(symphony_id):
    _ro_conn = database.get_ro_connection()
    try:
        logs = database.get_symphony_logs(symphony_id)
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        _ro_conn.close()

@app.route("/api/chart/<symphony_id>")
def get_chart_data(symphony_id):
    _ro_conn = database.get_ro_connection()
    try:
        chart_data = database.load_chart_history()
        symphony_data = chart_data.get("symphonies", {}).get(symphony_id, [])
        return jsonify({"status": "success", "data": symphony_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        _ro_conn.close()


@app.route("/api/triggers")
def api_triggers():
    _ro_conn = database.get_ro_connection()
    try:
        since = request.args.get("since")
        symphony_id = request.args.get("symphony_id")
        reason = request.args.get("reason")
        try:
            limit = int(request.args.get("limit", 100))
        except (ValueError, TypeError):
            limit = 100
        limit = min(limit, 500)
        rows = database.get_triggers(
            since=since,
            symphony_id=symphony_id,
            reason=reason,
            limit=limit,
        )
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        _ro_conn.close()


@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    try:
        row = database.read_fleet_alert()
        if row is not None:
            now_et = datetime.now(_ET).strftime("%Y-%m-%dT%H:%M:%S")
            payload = dict(row)
            payload["dismissed_at_et"] = now_et
            database.write_fleet_alert(payload)
        return jsonify({"status": "ok"})
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
    data = request.json or {}
    account_id = data.get("account_id")
    confirm_account_id = data.get("confirm_account_id")
    confirm_phrase = data.get("confirm_phrase")

    # Gate 1-4: confirmation validation before any env/credential check
    if not confirm_account_id:
        return jsonify({"status": "error", "message": "confirm_account_id is required"}), 400
    if confirm_account_id != account_id:
        return jsonify({"status": "error", "message": "confirm_account_id does not match account_id"}), 400
    if not confirm_phrase:
        return jsonify({"status": "error", "message": "confirm_phrase is required"}), 400
    if confirm_phrase != "LIQUIDATE":
        return jsonify({"status": "error", "message": "confirm_phrase must be exactly LIQUIDATE"}), 400

    env_vars = dotenv_values(".env")
    live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

    if not (account_id and env_vars.get("COMPOSER_KEY_ID")):
        return jsonify({"status": "error", "message": "Missing credentials or account ID."}), 400

    ts_et = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")

    # Audit: Discord alert + ERROR log on every invocation regardless of live_mode
    discord_url = env_vars.get("DISCORD_WEBHOOK_URL", "")
    if discord_url:
        try:
            requests.post(discord_url, json={
                "content": f"EMERGENCY LIQUIDATION TRIGGERED on {account_id} at {ts_et} ET (live={live_mode})"
            }, timeout=5)
        except Exception:
            pass

    _daemon_log.error(
        "EMERGENCY LIQUIDATION TRIGGERED on %s at %s ET (live=%s)",
        account_id, ts_et, live_mode,
    )

    if not live_mode:
        # Real-money safety gate: never spawn the liquidation thread when
        # LIVE_EXECUTION is False.  Return an explicit dry-run signal so the
        # operator dashboard can distinguish a successful no-op from a real
        # execution.
        return jsonify({
            "status": "dry_run",
            "dry_run": True,
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

# Keys whose values must never be echoed to the browser in GET /api/settings.
# Includes API credentials, webhook URLs, and account UUIDs (Alpaca account
# identifiers are sensitive — exposing them aids account enumeration attacks).
_MASKED_SETTINGS_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
})


def _mask_secret(value: str | None) -> str:
    """Return '' for any secret key — raw values must never reach the browser."""
    return ""


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Returns Globals from .env and Symphony Strategies from SQLite."""
    env_vars = dotenv_values(ENV_FILE_PATH)
    globals_data = {
        "LIVE_EXECUTION": env_vars.get("LIVE_EXECUTION", "False"),
        "EXECUTION_START_TIME": env_vars.get("EXECUTION_START_TIME", "09:30"),
        "EXIT_AUTHORITY": env_vars.get("EXIT_AUTHORITY", "per_symphony"),
    }
    # _MASKED_SETTINGS_KEYS is the single driver — adding a key there masks it automatically.
    for key in _MASKED_SETTINGS_KEYS:
        globals_data[key] = _mask_secret(env_vars.get(key))

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
        globals_payload = payload.get("globals", {})
        for key, val in globals_payload.items():
            set_key(ENV_FILE_PATH, key, str(val))

        # AC-P2.2.4: record timestamp when EXIT_AUTHORITY is changed so the sticky
        # restart notice can compare against daemon_started_at (panel BC H7).
        if "EXIT_AUTHORITY" in globals_payload:
            _changed_at = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            set_key(ENV_FILE_PATH, "_exit_authority_changed_at", _changed_at)

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

    # C2 Gate 4: locked-var guard — defense-in-depth; enforce_suggestion_allowlist
    # already rejects locked keys, but this gate survives any future allowlist drift.
    if suggestion_obj.config_key in locked_vars:
        return jsonify({"status": "rejected", "error": "locked var"}), 200

    # All gates passed — write the config change
    patched_params = dict(flat_params)
    patched_params[suggestion_obj.config_key] = suggestion_obj.suggested_value
    database.save_symphony_strategy(symphony_id, patched_params, locked_vars)
    return jsonify({"status": "accepted"})


@app.route("/ai-advisor/reject", methods=["POST"])
def ai_advisor_reject():
    """Record operator rejection — no config write."""
    return jsonify({"status": "rejected"})


@app.route("/api/autotune-runs", methods=["GET"])
def api_autotune_runs():
    """Return recent autotune run rows including all three Sharpe metrics."""
    _ro_conn = database.get_ro_connection()
    try:
        rows = database.get_all_autotune_runs(limit=50)
        return jsonify(rows)
    finally:
        _ro_conn.close()


if __name__ == "__main__":
    # Reconfigure stdout to UTF-8 so emoji/non-Latin-1 chars don't crash on
    # Windows (cp1252 default).  Guarded to __main__ so pytest's capture is
    # not affected when this module is imported during test collection.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    # Enforce daemon singleton BEFORE starting Flask or the scheduler thread.
    # If another AlphaBot is alive this call prints an error and exits non-zero.
    # If a stale pidfile exists (ungraceful prior kill) it is overwritten cleanly.
    _acquire_daemon_singleton(_PIDFILE_PATH)

    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"\nStarting Alpha Bot Control Center at http://localhost:{port}\n")

    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=port, debug=False, use_reloader=False)
