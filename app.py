"""Flask application for Planet Stopper Control Center with Account-Level settings."""

import atexit
import concurrent.futures
import io
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import dotenv as _dotenv_module
import psutil
import requests
import schedule
from dotenv import dotenv_values, set_key
from flask import Flask, abort, jsonify, render_template, request, session

import ai_advisor
import analytics
import database
import market_calendar
from market_calendar import get_market_state

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
    "volatility",  # Phase 2: annualized volatility (matches analytics.compute_quantstats_metrics)
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

# ---------------------------------------------------------------------------
# CSRF protection (A-3)
# ---------------------------------------------------------------------------
# Per-daemon-startup secret key.  Each process restart rotates the token,
# which is fine — the operator refreshes the dashboard naturally.
# TESTING mode skips enforcement so test_client() POST calls work without
# injecting a token header (tests use monkeypatch on _csrf_check_enabled).
app.secret_key = secrets.token_hex(32)

# Module-level flag — test suite sets this to False via monkeypatch to bypass
# CSRF checks on the test client.  Never set False in production code.
_csrf_check_enabled: bool = True

# One process-lifetime CSRF token.  Operator dashboards are single-user
# single-tab; a per-session token would require cookie round-trips that
# complicate the JS fetch() callers.  The attacker barrier is the token
# itself, not its rotation frequency.
_CSRF_TOKEN: str = secrets.token_hex(32)


def _validate_csrf() -> None:
    """Reject POST requests that lack the correct X-CSRF-Token header.

    Why a header rather than a form field: the dashboard POSTs JSON via
    fetch(); headers are same-origin only (browsers block cross-site JS from
    setting arbitrary request headers), so this is equivalent security to a
    synchronizer token for a localhost UI.  No new pip dependencies required.
    """
    if not _csrf_check_enabled:
        return
    token = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(token, _CSRF_TOKEN):
        _daemon_log.warning(
            "CSRF check failed on %s %s (token absent or incorrect)",
            request.method,
            request.path,
        )
        abort(403, description="CSRF token missing or invalid")


@app.route("/api/csrf-token", methods=["GET"])
def get_csrf_token():
    """Return the process-lifetime CSRF token for the operator dashboard.

    This endpoint is same-origin accessible only; the browser's SOP prevents
    cross-site pages from reading its response.
    """
    return jsonify({"csrf_token": _CSRF_TOKEN})


log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# Single-worker executor for dashboard background writes (fleet alert dismiss).
# A persistent worker thread owns the I/O so Flask request handlers return
# immediately without blocking on SQLite.  Single-worker is intentional:
# serialises writes so no two dismiss tasks race on fleet_alert_state.
_DISMISS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
# CC-003: register shutdown so in-flight dismiss writes are not abandoned on exit.
atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True)

# CC-NEW-001: serializes flush_resync's background load+modify+save against any
# other intra-process writer of the state DB.  This is an INTRA-PROCESS guard
# only: it serializes concurrent _flush_state_async submissions running on the
# _DISMISS_EXECUTOR worker within this Flask daemon.  The engine
# (alpha_bot_execution.py) is spawned in a SEPARATE OS process and cannot see
# this threading.Lock; cross-process isolation between the daemon and the engine
# subprocess is provided by SQLite WAL transaction isolation, not by this lock.
_FLUSH_STATE_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# AI Advisor chat — cost-DoS guard constants (AC-2)
# ---------------------------------------------------------------------------
# Maximum JSON-encoded size of the entire request body.  Limits flood payloads
# before any body parsing occurs.  Werkzeug enforces MAX_CONTENT_LENGTH at the
# WSGI layer (returns 413 automatically); we wire the same constant so the
# limit is expressed once (AC-2 / B-1).
CHAT_MAX_REQUEST_BODY_BYTES: int = 65536  # 64 KB
app.config["MAX_CONTENT_LENGTH"] = CHAT_MAX_REQUEST_BODY_BYTES

# Maximum character count for the operator's chat message.  Prevents a single
# oversized message from exhausting the LLM token budget.
CHAT_MAX_MESSAGE_CHARS: int = 2000

# Maximum JSON-encoded size of the artifact dict sent with each request.
# M1–M4 artifacts are typically <1 KB; 8 KB is generous but bounded.
CHAT_MAX_ARTIFACT_BYTES: int = 8192  # 8 KB

# Sliding-window rate limit: max requests allowed per remote IP within the window.
CHAT_RATE_LIMIT_MAX_REQUESTS: int = 10

# Duration of the sliding rate-limit window in seconds.
CHAT_RATE_LIMIT_WINDOW_SECONDS: int = 60

# Maximum distinct IPs tracked simultaneously in _CHAT_RATE_LIMITER.
# Bounds memory growth; the operator dashboard is single-user and rarely
# sees more than a handful of distinct source IPs (B-2).
CHAT_RATE_LIMITER_MAX_TRACKED_IPS: int = 1000

# Per-IP timestamp deque for the sliding-window rate limiter.
# Maps remote_addr -> collections.deque of float timestamps (time.time()).
# Module-level so state persists across requests within a process lifetime.
import collections as _collections  # noqa: E402 — stdlib, late import for locality
_CHAT_RATE_LIMITER: dict = {}

_daemon_log = logging.getLogger("alphabot")
_daemon_log.setLevel(logging.DEBUG)
_daemon_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_daemon_fh.setLevel(logging.DEBUG)
_daemon_log.addHandler(_daemon_fh)


@app.before_request
def _csrf_before_request() -> None:
    """Enforce CSRF token on every mutating (POST) request (A-3)."""
    if request.method == "POST":
        _validate_csrf()


COMPOSER_BASE_URL = "https://api.composer.trade/api/v0.1"

# Recorded at import time — used by /api/state daemon_started_at field (AC-P2.12.2)
# and the sticky restart-notice comparison (AC-P2.2.4 BC H7).
_DAEMON_STARTED_AT: str = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

# TTL cache for account-level Composer total-stats.  Populated by
# _refresh_account_totals() on the minute scheduler; read by
# _compute_portfolio_strip() so dashboard requests never block on a live call.
_account_totals_cache: dict = {}

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
        with open(path, encoding="ascii") as fh:
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
    a live Planet Stopper process.

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
                f"Another Planet Stopper daemon is already running (PID {stored_pid}); "
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
        print(
            f"[retention] pruned {shadow_deleted} old shadow_history rows (>{shadow_retention_days}d)"
        )


def _refresh_account_totals() -> None:
    """Fetch Composer account-level total-stats and populate _account_totals_cache.

    Called by the minute scheduler — must never raise (swallows all exceptions).
    On non-200 or any exception the existing cache is left unchanged (stale > empty).
    Auth pattern mirrors alpha_bot_execution.get_composer_headers().
    """
    try:
        env_vars = dotenv_values(ENV_FILE_PATH)
        key_id = env_vars.get("COMPOSER_KEY_ID") or os.environ.get("COMPOSER_KEY_ID", "")
        secret = env_vars.get("COMPOSER_SECRET") or os.environ.get("COMPOSER_SECRET", "")
        account_id = (
            env_vars.get("ACCOUNT_ROTH")
            or env_vars.get("ACCOUNT_INDIVIDUAL")
            or env_vars.get("ACCOUNT_TRAD")
            or os.environ.get("ACCOUNT_ROTH", "")
            or os.environ.get("ACCOUNT_INDIVIDUAL", "")
            or os.environ.get("ACCOUNT_TRAD", "")
            or ""
        ).strip()
        url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/total-stats"
        headers = {
            "x-api-key-id": key_id,
            "authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _account_totals_cache["portfolio_value"] = data["portfolio_value"]
            # simple_return (not time_weighted_return) matches Composer's displayed
            # portfolio return for accounts with net deposits > 0. TWR diverges by
            # ~5 pp when cash flows exist; operators should compare against
            # Composer's "Total return" figure, which also uses simple_return.
            _account_totals_cache["portfolio_cr"] = data["simple_return"] * 100.0
            # Cache todays_percent_change from Composer so the portfolio TC can use
            # the authoritative denominator (includes cash) rather than the
            # per-symphony sum which excludes uninvested cash.
            if "todays_percent_change" in data:
                _account_totals_cache["portfolio_tc"] = data["todays_percent_change"] * 100.0
            # D-02: cache Composer portfolio-level MDD (peak-to-trough on aggregate
            # equity curve) so we never substitute value-weighted average of per-symphony MDDs.
            _metrics = data.get("metrics") or {}
            if "max_drawdown" in _metrics:
                _account_totals_cache["portfolio_mdd"] = float(_metrics["max_drawdown"]) * 100.0
        else:
            _daemon_log.warning(
                "_refresh_account_totals: Composer returned %s — cache unchanged",
                resp.status_code,
            )
    except Exception as _exc:
        _daemon_log.error(
            "_refresh_account_totals failed — account totals cache unchanged: %s",
            _exc,
            exc_info=True,
        )


def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    schedule.every().minute.at(":00").do(_refresh_account_totals)
    schedule.every().day.at("02:00").do(_run_trigger_retention)
    while True:
        schedule.run_pending()
        time.sleep(1)


# --- 2. Web Dashboard Routes ---
@app.route("/")
def dashboard():
    api_state = get_api_state_dict()
    bot_state = api_state.get("bot_state") or database.load_state()
    portfolio_strip = api_state.get("portfolio_strip") or {}
    meta = api_state.get("meta") or _build_meta(
        bot_state,
        market_state=get_market_state(datetime.now(_ET)),
        portfolio_strip=portfolio_strip,
    )

    vars_locked_count = 0
    for sym_id, sym_data in bot_state.items():
        if not isinstance(sym_data, dict) or "name" not in sym_data:
            continue
        name = database.normalize_name(sym_data.get("name", ""))
        strategy = database.get_symphony_strategy(name)
        if strategy:
            vars_locked_count += len(strategy.get("locked_vars") or [])

    # Partition symphonies into active (armed/triggered) and standby.
    symphonies = api_state.get("symphonies") or [
        v for v in bot_state.values() if isinstance(v, dict) and "name" in v
    ]
    # Ensure every symphony dict carries its own key as 'id' so Jinja data-sym-id renders correctly.
    for _sym_key, _sym_val in bot_state.items():
        if isinstance(_sym_val, dict) and "name" in _sym_val and not _sym_val.get("id"):
            _sym_val["id"] = _sym_key

    # FIX-23: Enrich each symphony with _cr/_tc/_mdd from analytics so cards render live returns.
    _dash_today = datetime.now(_ET).strftime("%Y-%m-%d")
    for _s in symphonies:
        if not isinstance(_s, dict):
            continue
        _sym_id = _s.get("id", "")
        _cr_val = _s.get("current_return") or 0.0
        _val = _s.get("current_value") or 0.0
        _sym_dict = {
            "id": _sym_id,
            "value": _val,
            "last_percent_change": _cr_val / 100.0,
            "simple_return": _s.get("simple_return"),
            "net_deposits": _s.get("net_deposits"),
            "time_weighted_return": _s.get("time_weighted_return"),
            "max_drawdown": _s.get("max_drawdown"),
            "trading_day": _dash_today,
        }
        def _safe_analytics(fn, *args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                if not isinstance(result, dict):
                    return {"if_held": 0.0, "dry_run": 0.0}
                return {k: (v if v is not None else 0.0) for k, v in result.items()}
            except Exception:
                return {"if_held": 0.0, "dry_run": 0.0}

        if "_cr" not in _s:
            _s["_cr"] = _safe_analytics(analytics.get_symphony_cumulative_return, _sym_dict, _s, trading_day=_dash_today)
        if "_tc" not in _s:
            _s["_tc"] = _safe_analytics(analytics.get_symphony_today_change, _sym_dict, _s, trading_day=_dash_today)
        if "_mdd" not in _s:
            _s["_mdd"] = _safe_analytics(analytics.get_symphony_max_drawdown, _sym_dict, _s, trading_day=_dash_today)

    active_syms = [s for s in symphonies if s.get("armed") or s.get("tp_armed") or s.get("para_armed") or s.get("triggered")]
    standby_syms = [s for s in symphonies if not (s.get("armed") or s.get("tp_armed") or s.get("para_armed") or s.get("triggered"))]

    # Build accounts_map for panic modal (account_id → label).
    _env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
    accounts_map = {}
    for _k, _lbl in (
        (_env.get("ACCOUNT_INDIVIDUAL", "").strip(), "Individual"),
        (_env.get("ACCOUNT_ROTH", "").strip(), "Roth IRA"),
        (_env.get("ACCOUNT_TRAD", "").strip(), "Trad. IRA"),
    ):
        if _k:
            accounts_map[_k] = _lbl

    # M2 CVaR diagnostic — read the latest diagnostic row for the first symphony.
    # Dashboard is a read-only observer (arch constraint 2); this is a pure DB read.
    # Sentinel: cvar_diagnostic=None when the table has no row yet (Phase-1 warm-up).
    #
    # CVAR-001 Phase-1 scope limit (sprint-2-audit a6e4d9f8): only the FIRST symphony's
    # CVaR diagnostic is surfaced on the dashboard. Multi-symphony portfolios silently
    # omit other symphonies' diagnostics. This is intentional for Phase-1 — the M2
    # CVaR diagnostic is a proof-of-concept single-symphony display. Phase-2 will expand
    # this to a per-symphony dict passed to the template for multi-row rendering.
    # TODO(Phase-2): replace _first_sym_id with a full dict keyed by symphony_id.
    cvar_diagnostic = None
    try:
        _first_sym_id = next(
            (k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v),
            None,
        )
        if _first_sym_id:
            cvar_diagnostic = database.read_cvar_diagnostic_for_symphony(
                _first_sym_id
            )
    except Exception:
        pass  # non-blocking: dashboard renders without CVaR if the read fails

    _today_close = market_calendar.session_close(datetime.now(_ET).date())
    session_close_display = _today_close.strftime("%H:%M ET")

    return render_template(
        "index.html",
        vars_locked_count=vars_locked_count,
        active_route="dashboard",
        meta=meta,
        bot_state=bot_state,
        portfolio_strip=portfolio_strip,
        active_syms=active_syms,
        standby_syms=standby_syms,
        accounts_map=accounts_map,
        cvar_diagnostic=cvar_diagnostic,
        session_close_display=session_close_display,
    )


_MARKET_STATE_UNSET = object()


def _build_meta(
    state_data: dict,
    next_run_seconds: int = 0,
    market_state=_MARKET_STATE_UNSET,
    portfolio_strip: dict | None = None,
) -> dict:
    """Build the meta object for /api/state responses (AC-C1 Foundation)."""
    # Use module-qualified call so test patches on dotenv.dotenv_values take effect.
    env_vars = _dotenv_module.dotenv_values(ENV_FILE_PATH)
    live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

    # Count only symphony dicts (those with a 'name' key).
    symphony_entries = [v for v in state_data.values() if isinstance(v, dict) and "name" in v]
    tracked = len(symphony_entries)
    # Armed = any armed variant (trailing/TP/para) that has NOT yet triggered.
    # A triggered symphony retains armed=True in the state dict; excluding triggered
    # here prevents double-counting in the hero quick-view "X Armed" badge.
    armed = sum(
        1
        for s in symphony_entries
        if (s.get("armed") or s.get("tp_armed") or s.get("para_armed"))
        and not s.get("triggered")
    )
    triggered = sum(1 for s in symphony_entries if s.get("triggered"))

    # Account label + short UUID from env.
    acc_roth = env_vars.get("ACCOUNT_ROTH", "").strip()
    acc_ind = env_vars.get("ACCOUNT_INDIVIDUAL", "").strip()
    acc_trad = env_vars.get("ACCOUNT_TRAD", "").strip()
    account_map = {}
    if acc_ind:
        account_map[acc_ind] = "Individual"
    if acc_roth:
        account_map[acc_roth] = "Roth IRA"
    if acc_trad:
        account_map[acc_trad] = "Trad. IRA"

    first_uuid, first_label = "", "Account"
    if account_map:
        first_uuid = next(iter(account_map))
        first_label = account_map[first_uuid]

    if market_state is _MARKET_STATE_UNSET:
        raise TypeError("_build_meta() missing required argument: 'market_state'")
    # market_state_label — same condition as the dot (market_state == "open")
    # so both always agree.
    ms_label = "Market open" if market_state == "open" else "Market closed"

    clock_et = datetime.now(_ET).strftime("%I:%M:%S %p ET")
    next_run_str = f"{next_run_seconds // 60:02d}:{next_run_seconds % 60:02d}"

    # Build triggers_today counts from exit_triggers (D-DAT-R04).
    _today_start = datetime.now(_ET).strftime("%Y-%m-%d") + "T00:00:00Z"
    _triggers_today: dict = {"trailing_stop": 0, "take_profit": 0, "vwap": 0}
    try:
        _today_exits = database.get_triggers(since=_today_start, limit=500)
        for _t in _today_exits:
            _reason = (_t.get("triggered_reason") or "").lower()
            if "trailing" in _reason:
                _triggers_today["trailing_stop"] += 1
            elif "take" in _reason or "profit" in _reason:
                _triggers_today["take_profit"] += 1
            elif "vwap" in _reason:
                _triggers_today["vwap"] += 1
    except Exception:
        pass

    # Build meta.portfolio from portfolio_strip (AC-C2 Hero chart data).
    ps = portfolio_strip or {}
    tc_data = ps.get("today_change") or {}
    cr_data = ps.get("cumulative_return") or {}
    mdd_data = ps.get("max_drawdown") or {}
    _hist_dates = ps.get("hist_dates", [])
    # Data-sufficiency: meaningful risk metrics require >= 30 trading days of history
    # (Bailey/de-Prado 2014 threshold). Emit an explicit flag so the UI can show
    # "N/A — insufficient history" rather than fabricated or misleading values.
    _insufficient_history = len(_hist_dates) < 30
    portfolio_meta = {
        "tc": tc_data.get("dry_run", 0.0),
        "tc_if_held": tc_data.get("if_held", 0.0),
        "cr": cr_data.get("dry_run", 0.0),
        "cr_if_held": cr_data.get("if_held", 0.0),
        "mdd": mdd_data.get("dry_run", 0.0),
        "mdd_if_held": mdd_data.get("if_held", 0.0),
        "hist_dates": _hist_dates,
        "hist_bot": ps.get("hist_bot", []),
        "hist_held": ps.get("hist_held", []),
        "hist_source": ps.get("hist_source", "post_mortem"),
        "data_as_of": ps.get("data_as_of", ""),
        "account_value": round(ps.get("account_value") or 0.0, 2),
        "insufficient_history": _insufficient_history,
        "history_days": len(_hist_dates),
        # Phase 2b: portfolio-level annualized vol (fraction scale) — feeds the
        # hero Ann. Vol vs-row. None-safe: stays None when no shadow history.
        "vol_bot": ps.get("vol_bot"),
        "vol_held": ps.get("vol_held"),
    }

    return {
        "mode": "LIVE" if live_mode else "DRY RUN",
        "system_online": market_state == "open",
        "tracked": tracked,
        "armed": armed,
        "triggered": triggered,
        "next_run": next_run_str,
        "account": {
            "label": first_label,
            "uuid_short": first_uuid[:8] if first_uuid else "",
        },
        "market_state": market_state,
        "market_state_label": ms_label,
        "clock_et": clock_et,
        "portfolio": portfolio_meta,
        "triggers_today": _triggers_today,
    }


def _compute_portfolio_strip(bot_state: dict, trading_day: str | None = None) -> dict:
    """Compute portfolio_strip from bot_state using analytics helpers.

    Shared by get_api_state_dict() (Jinja render path) and get_state() (JSON
    poll path) so both paths emit identical portfolio_strip shape.  This closes
    the systemic 0.00% everywhere defect (FP-T1-01).
    """
    if trading_day is None:
        trading_day = datetime.now(_ET).strftime("%Y-%m-%d")

    symphony_keys = [k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v]
    symphonies_list = []
    for k in symphony_keys:
        s = bot_state[k]
        cr = s.get("current_return") or 0.0
        val = s.get("current_value") or 0.0
        symphonies_list.append(
            {
                "id": k,
                "value": val,
                "last_percent_change": cr / 100.0,
                "simple_return": s.get("simple_return"),
                "net_deposits": s.get("net_deposits"),
                "time_weighted_return": s.get("time_weighted_return"),
                "max_drawdown": s.get("max_drawdown"),
                "trading_day": trading_day,
            }
        )

    # Use cached Composer account-level value when available; per-symphony sum as fallback.
    if "portfolio_value" in _account_totals_cache:
        account_value = _account_totals_cache["portfolio_value"]
    else:
        account_value = sum(
            v.get("current_value") or 0.0
            for v in bot_state.values()
            if isinstance(v, dict)
        )

    try:
        if "portfolio_cr" in _account_totals_cache:
            # B-1 fix: put Bot (dry_run) on the same account basis as Held (if_held).
            # Held = Composer simple_return (cash-inclusive denominator).
            # Bot = VW per-symphony guard divergence scaled to account basis so that
            # guard_alpha = dry_run - if_held is a scope-clean apples-to-apples delta.
            # guard_delta is measured on the VW basis first (dry_run and if_held share
            # the same symphony-value denominator), then scaled by invested_frac.
            _vw_cr = analytics.get_portfolio_cumulative_return(
                symphonies_list, bot_state, trading_day=trading_day
            )
            _symphony_value_sum = sum(s.get("value") or 0.0 for s in symphonies_list)
            cumulative_return: dict | None = analytics.get_portfolio_cumulative_return_account_basis(
                _vw_cr,
                _account_totals_cache["portfolio_cr"],
                account_value,
                _symphony_value_sum,
            )
        else:
            cumulative_return = analytics.get_portfolio_cumulative_return(
                symphonies_list, bot_state, trading_day=trading_day
            )

        # D-01: use the Composer-sourced today-change (includes cash in denominator)
        # when available; otherwise fall back to the per-symphony value-weighted sum
        # which excludes uninvested cash and will be slightly off.
        if "portfolio_tc" in _account_totals_cache:
            today_change: dict = {
                "if_held": _account_totals_cache["portfolio_tc"],
                "dry_run": analytics.get_portfolio_today_change(
                    symphonies_list, bot_state, trading_day=trading_day
                ).get("dry_run"),
            }
        else:
            today_change = analytics.get_portfolio_today_change(
                symphonies_list, bot_state, trading_day=trading_day
            )

        # D-02: use Composer portfolio-level MDD (peak-to-trough on aggregate equity
        # curve) when available. The value-weighted average of per-symphony MDDs is
        # mathematically wrong — portfolio drawdowns can exceed any constituent MDD
        # when declines co-occur. Fall back to value-weighted only when cache is cold.
        #
        # D8 sign convention: the operator-facing portfolio MDD is canonically a
        # POSITIVE magnitude. The warm-cache portfolio_mdd is written in
        # _refresh_account_totals from Composer's API metrics.max_drawdown field
        # (app.py:272), which is conventionally NEGATIVE; abs()-convert it here at
        # the consumer boundary so the operator-facing value is positive
        # magnitude. The cold-cache path flows through
        # analytics.get_portfolio_max_drawdown, already positive magnitude — both
        # branches must agree on sign regardless of cache warmth.
        if "portfolio_mdd" in _account_totals_cache:
            max_drawdown: dict = {
                "if_held": abs(_account_totals_cache["portfolio_mdd"]),
                "dry_run": analytics.get_portfolio_max_drawdown(
                    symphonies_list, bot_state, trading_day=trading_day
                ).get("dry_run"),
            }
        else:
            max_drawdown = analytics.get_portfolio_max_drawdown(
                symphonies_list, bot_state, trading_day=trading_day
            )

        # Phase 2b: portfolio-level annualized volatility from the COMBINED
        # portfolio return series (captures inter-symphony correlations — the
        # correct method vs averaging per-symphony vols). Both bot and held read
        # None (not 0.0) when no shadow history — a real 0.0 would be ambiguous
        # with a genuine zero-vol result.
        # vol_held stays None: no portfolio-level held daily return series exists in
        # shadow_history (no live_return column). Setting vol_held = vol_bot would
        # fabricate a false tie (delta always 0). The hero row stubs held to '—'
        # via the has_vol guard — honest, not misleading. Deriving held vol from
        # shadow_history.current_return is deferred to a future cycle.
        vol_bot: float | None = None
        vol_held: float | None = None
        _shadow_result = analytics.get_portfolio_daily_returns_from_shadow()
        if _shadow_result is not None:
            _, _port_daily_returns = _shadow_result
            vol_bot = analytics.compute_portfolio_annualized_vol(_port_daily_returns)

        return {
            "today_change": today_change,
            "cumulative_return": cumulative_return,
            "max_drawdown": max_drawdown,
            "account_value": account_value,
            "vol_bot": vol_bot,
            "vol_held": vol_held,
            "data_as_of": datetime.now(_ET).strftime("%H:%M ET"),
        }
    except Exception as _exc:
        _daemon_log.error(
            "_compute_portfolio_strip failed — portfolio strip will be null: %s", _exc, exc_info=True
        )
        return {
            "today_change": None,
            "cumulative_return": None,
            "max_drawdown": None,
            "account_value": account_value,
            "vol_bot": None,
            "vol_held": None,
            "data_as_of": datetime.now(_ET).strftime("%H:%M ET"),
        }


def get_api_state_dict() -> dict:
    """
    Return the core state dict consumed by /api/state and testable without HTTP.

    Additive fields (AC-P2.12.2): port_state, exit_authority, daemon_started_at.
    No existing field is renamed or removed.
    """
    bot_state = database.load_state()
    exit_authority = os.getenv("EXIT_AUTHORITY", "per_symphony")

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

    portfolio_strip = _compute_portfolio_strip(bot_state)

    return {
        "bot_state": bot_state,
        "is_locked": is_locked,
        "port_state": port_state,
        "exit_authority": exit_authority,
        "daemon_started_at": _DAEMON_STARTED_AT,
        "portfolio_strip": portfolio_strip,
    }


@app.route("/api/state")
def get_state():
    try:
        _ro_conn = database.get_ro_connection()
        market_state = get_market_state(datetime.now(_ET))

        # AC-P2.12.2: additive fields — computed once, merged into every response branch.
        _api_state = get_api_state_dict()
        _additive_env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
        _additive_live_mode = _additive_env.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")
        _additive = {
            "port_state": _api_state.get("port_state", {}),
            "exit_authority": _api_state.get("exit_authority", {}),
            "daemon_started_at": _DAEMON_STARTED_AT,
            "live_mode": _additive_live_mode,
        }
        # Allow test stubs to inject portfolio_strip via get_api_state_dict return value.
        # _api_state_has_strip tracks whether the caller explicitly provided portfolio_strip
        # (used to decide whether to include it in the waiting branch response).
        _api_state_has_strip = "portfolio_strip" in _api_state
        _injected_portfolio_strip = _api_state.get("portfolio_strip")

        # D-03: When portfolio_strip has no hist arrays, build them from a continuous
        # daily source. Preference order:
        #   1. shadow_history (written every cycle for every tracked symphony — continuous).
        #   2. post_mortem files (written only on exit-trigger days — biased partial curve).
        # Shadow_history grows over time; once it accumulates sufficient days the hero chart
        # terminal value will converge to the real portfolio CR.
        _ps_dict = _injected_portfolio_strip if isinstance(_injected_portfolio_strip, dict) else {}
        if not (_ps_dict and _ps_dict.get("hist_dates")):
            try:
                _analytics_strip = None

                # Attempt 1: continuous series from shadow_history.
                _shadow_strip = None
                _shadow_result = analytics.get_portfolio_daily_returns_from_shadow()
                if _shadow_result is not None:
                    _shadow_dates, _shadow_daily = _shadow_result
                    _hist_series: list[float] = []
                    _running = 1.0
                    for _d in _shadow_daily:
                        _running *= (1.0 + _d / 100.0)
                        _hist_series.append((_running - 1.0) * 100.0)
                    _shadow_strip = {
                        "hist_dates": _shadow_dates,
                        # Both bot and held use the same shadow series since
                        # shadow_return tracks the live portfolio return continuously;
                        # divergence between bot-exited and if-held will appear once
                        # post-trigger shadow tracking matures.
                        "hist_bot": _hist_series,
                        "hist_held": _hist_series,
                        "hist_source": "shadow_history",
                    }

                # Attempt 2: post_mortem (exit-trigger days only).
                # weight_by="value" matches real post_mortem shape; fall back to
                # "weight" so tests that inject a _CHART_ARCHIVE_STUB with a
                # "weight" field also produce results.
                _pm_strip = None
                _hist_data = analytics.get_history_with_cache_invalidation(
                    base_dir=analytics._POST_MORTEMS_DIR
                )
                if _hist_data:
                    _agg_dates, _agg_held_daily, _agg_bot_daily = (
                        analytics.compute_aggregate_returns(_hist_data)
                    )
                    if not _agg_dates:
                        # Retry with the "weight" field name used by chart_archive stubs.
                        _agg_dates, _agg_held_daily, _agg_bot_daily = (
                            analytics.compute_aggregate_returns(_hist_data, weight_by="weight")
                        )
                    if _agg_dates:
                        _hist_bot = []
                        _hist_held = []
                        _running_bot = 1.0
                        _running_held = 1.0
                        for _bot_d, _held_d in zip(_agg_bot_daily, _agg_held_daily):
                            _running_bot *= (1.0 + _bot_d / 100.0)
                            _running_held *= (1.0 + _held_d / 100.0)
                            _hist_bot.append((_running_bot - 1.0) * 100.0)
                            _hist_held.append((_running_held - 1.0) * 100.0)
                        _pm_strip = {
                            "hist_dates": _agg_dates,
                            "hist_bot": _hist_bot,
                            "hist_held": _hist_held,
                            "hist_source": "post_mortem",
                        }

                # Pick the source with more days; shadow preferred on tie (continuous).
                _shadow_days = len((_shadow_strip or {}).get("hist_dates", []))
                _pm_days = len((_pm_strip or {}).get("hist_dates", []))
                if _shadow_days >= _pm_days and _shadow_strip is not None:
                    _analytics_strip = _shadow_strip
                elif _pm_strip is not None:
                    _analytics_strip = _pm_strip
                else:
                    _analytics_strip = _shadow_strip  # may still be None

                if _analytics_strip is not None:
                    if _ps_dict:
                        _injected_portfolio_strip = {**_analytics_strip, **_ps_dict}
                    else:
                        _injected_portfolio_strip = _analytics_strip
            except Exception as _hist_exc:
                _daemon_log.error(
                    "get_state() hist-series build failed — hero chart will be empty: %s",
                    _hist_exc,
                    exc_info=True,
                )

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
                _alert = (
                    _alert_row
                    if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None)
                    else None
                )
                _state: dict = {}
                for _acc_entries in (snapshot.get("accounts_map") or {}).values():
                    for _sym in _acc_entries or []:
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
                _is_desc = _sort_dir == "desc"

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
                    return (
                        s.get("current_return") if s.get("current_return") is not None else -999.0
                    )

                for _acc_id in _snap_accounts_map:
                    _syms = _snap_accounts_map[_acc_id]
                    if _sort_col == "mc_prob":
                        _syms.sort(
                            key=lambda s: (
                                s.get("mc_prob") if s.get("mc_prob") is not None else -999.0
                            ),
                            reverse=_is_desc,
                        )
                    elif _sort_col == "status":
                        _syms.sort(key=_frozen_status_rank, reverse=_is_desc)
                    elif _sort_col == "stop_level":
                        _syms.sort(
                            key=lambda s: (
                                s.get("triggered_at_stop")
                                if s.get("triggered") and s.get("triggered_at_stop") is not None
                                else (
                                    s.get("stop_trigger")
                                    if s.get("stop_trigger") is not None
                                    else -999.0
                                )
                            ),
                            reverse=_is_desc,
                        )
                    elif _sort_col == "current_return":
                        _syms.sort(key=_frozen_exit_ret, reverse=_is_desc)
                    elif _sort_col == "shadow_hwm":
                        _syms.sort(key=lambda s: s.get("shadow_hwm", -999.0), reverse=_is_desc)
                    elif _sort_col == "shadow":
                        _syms.sort(
                            key=lambda s: (
                                s.get("current_return")
                                if s.get("current_return") is not None
                                else -999.0
                            ),
                            reverse=_is_desc,
                        )
                    else:  # name (default)
                        _syms.sort(
                            key=lambda s: (s.get("name") or s.get("id", "")).lower(),
                            reverse=_is_desc,
                        )

                # Enrich each snapshot symphony with TC/CR/MDD analytics, using
                # snapshot["trading_day"] so analytics reads from shadow_history for
                # that day (R14 contract — NOT today's date).
                _snap_trading_day = snapshot.get("trading_day")
                for _acc_syms in _snap_accounts_map.values():
                    for _sym in _acc_syms or []:
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
                            _sym["_tc"] = analytics.get_symphony_today_change(
                                _sym_dict, _sym, trading_day=_snap_trading_day
                            )
                        except (KeyError, TypeError, ValueError):
                            _sym["_tc"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_cr"] = analytics.get_symphony_cumulative_return(
                                _sym_dict, _sym, trading_day=_snap_trading_day
                            )
                        except (KeyError, TypeError, ValueError):
                            _sym["_cr"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_mdd"] = analytics.get_symphony_max_drawdown(
                                _sym_dict, _sym, trading_day=_snap_trading_day
                            )
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
                    for _sym_n in _acc_syms_n or []:
                        if isinstance(_sym_n, dict):
                            for _field, _default in _FROZEN_SYM_DEFAULTS.items():
                                _sym_n.setdefault(_field, _default)

                # On-the-fly portfolio_strip recompute — authoritative, never pass-through.
                # Recompute from accounts_map so stale/None captured values are never surfaced.
                # R14: use snapshot.trading_day, not today's date.
                _snap_symphonies_list = []
                _snap_bot_state = {}
                for _acc_syms_r in _snap_accounts_map.values():
                    for _sym_r in _acc_syms_r or []:
                        if not isinstance(_sym_r, dict):
                            continue
                        _sid_r = _sym_r.get("id", "")
                        _cr_r = _sym_r.get("current_return") or 0.0
                        _val_r = _sym_r.get("current_value") or 0.0
                        _snap_symphonies_list.append(
                            {
                                "id": _sid_r,
                                "value": _val_r,
                                "last_percent_change": _cr_r / 100.0,
                                "simple_return": _sym_r.get("simple_return"),
                                "net_deposits": _sym_r.get("net_deposits"),
                                "time_weighted_return": _sym_r.get("time_weighted_return"),
                                "max_drawdown": _sym_r.get("max_drawdown"),
                                "trading_day": _snap_trading_day,
                            }
                        )
                        _snap_bot_state[_sid_r] = {
                            "current_value": _val_r,
                            "current_return": _cr_r,
                            "name": _sym_r.get("name"),
                            "account": _sym_r.get("account"),
                        }
                try:
                    _snap_cr = analytics.get_portfolio_cumulative_return(
                        _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                    )
                    if "portfolio_cr" in _account_totals_cache:
                        _snap_cr = {
                            "if_held": _account_totals_cache["portfolio_cr"],
                            "dry_run": _snap_cr.get("dry_run") if _snap_cr else None,
                        }
                    _portfolio_strip = {
                        "today_change": analytics.get_portfolio_today_change(
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                        ),
                        "cumulative_return": _snap_cr,
                        "max_drawdown": analytics.get_portfolio_max_drawdown(
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                        ),
                        "account_value": (
                            _account_totals_cache["portfolio_value"]
                            if "portfolio_value" in _account_totals_cache
                            else sum(
                                v.get("current_value") or 0.0
                                for v in _snap_bot_state.values()
                                if isinstance(v, dict)
                            )
                        ),
                        "data_as_of": datetime.now(_ET).strftime("%H:%M ET"),
                    }
                except Exception:
                    _portfolio_strip = {
                        "today_change": None,
                        "cumulative_return": None,
                        "max_drawdown": None,
                        "account_value": _account_totals_cache.get("portfolio_value"),
                        "data_as_of": datetime.now(_ET).strftime("%H:%M ET"),
                    }

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

                _frozen_env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
                _frozen_live_mode = _frozen_env.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")
                return jsonify(
                    {
                        "status": "active",
                        "market_state": market_state,
                        "frozen_at": snapshot.get("captured_at_et"),
                        "data_as_of": snapshot.get("data_as_of"),
                        "state": _state,
                        "bot_state": _state,
                        "live_mode": _frozen_live_mode,
                        "portfolio_strip": _portfolio_strip,
                        "shadow_divergence": sd,
                        "accounts_map": snapshot.get("accounts_map"),
                        "fleet_correlation_alert": _alert,
                        "html": _frozen_html,
                        "meta": _build_meta(
                            _state,
                            market_state=market_state,
                            portfolio_strip=_injected_portfolio_strip or _portfolio_strip,
                        ),
                        **_additive,
                    }
                )

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
            _alert = (
                _alert_row
                if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None)
                else None
            )
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
                waiting_resp["notice"] = (
                    "No closing snapshot yet — waiting for first market close at 16:00 ET."
                )
            # Only pass hist arrays to _build_meta — skip today_change/cumulative_return etc
            # which may be raw floats (not dicts) in the test stub or during warmup.
            _wait_strip = None
            if isinstance(_injected_portfolio_strip, dict) and _injected_portfolio_strip.get("hist_dates"):
                _wait_strip = {
                    "hist_dates": _injected_portfolio_strip.get("hist_dates", []),
                    "hist_bot": _injected_portfolio_strip.get("hist_bot", []),
                    "hist_held": _injected_portfolio_strip.get("hist_held", []),
                }
            _waiting_body = {**waiting_resp, "bot_state": _api_state.get("bot_state", {}), "meta": _build_meta({}, market_state=market_state, portfolio_strip=_wait_strip), **_additive}
            if _api_state_has_strip:
                _waiting_body["portfolio_strip"] = _injected_portfolio_strip
            return jsonify(_waiting_body)

        # FP-T3-03 backend: optional ?account=<uuid> filter on bot_state.
        _account_filter = request.args.get("account")
        if _account_filter:
            state_data = {
                k: v for k, v in state_data.items()
                if not isinstance(v, dict) or v.get("account") == _account_filter
            }

        env_vars = dotenv_values(".env")
        live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

        # Seconds until the next engine cycle. The minute scheduler fires at
        # :00 of every minute, so the wait is always wall-clock time to the
        # next minute boundary. Computed directly here — the prior
        # schedule.get_jobs() introspection returned a stale 0 on a live daemon.
        _now = datetime.now()
        _secs_into = _now.second + _now.microsecond / 1_000_000
        next_run_seconds = max(0, int(60 - _secs_into))

        # Render HTML for UI
        # AC-4d: name-guard so phantom non-symphony top-level dicts (e.g.
        # last_market_close_snapshot) cannot leak into the cards/standby list.
        # Matches the SSR/meta paths (app.py:433/544) which already require "name".
        symphony_keys = [
            k for k in state_data.keys()
            if isinstance(state_data[k], dict) and "name" in state_data[k]
        ]
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

        if acc_ind:
            account_labels[acc_ind] = "Individual"
        if acc_roth:
            account_labels[acc_roth] = "Roth IRA"
        if acc_trad:
            account_labels[acc_trad] = "Trad. IRA"

        # Sorting logic
        sort_col = request.args.get("sortCol", "name")
        sort_dir = request.args.get("sortDir", "asc")
        is_desc = sort_dir == "desc"

        def get_status_rank(s):
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

        def get_exit_ret(s):
            if s.get("triggered"):
                return (
                    s.get("triggered_at_return")
                    if s.get("triggered_at_return") is not None
                    else (s.get("current_return") or -999.0)
                )
            return s.get("current_return") if s.get("current_return") is not None else -999.0

        for acc_id in accounts_map:
            if sort_col == "mc_prob":
                accounts_map[acc_id].sort(
                    key=lambda s: s.get("mc_prob") if s.get("mc_prob") is not None else -999.0,
                    reverse=is_desc,
                )
            elif sort_col == "status":
                accounts_map[acc_id].sort(key=get_status_rank, reverse=is_desc)
            elif sort_col == "stop_level":
                accounts_map[acc_id].sort(
                    key=lambda s: (
                        s.get("triggered_at_stop")
                        if s.get("triggered") and s.get("triggered_at_stop") is not None
                        else (
                            s.get("stop_trigger") if s.get("stop_trigger") is not None else -999.0
                        )
                    ),
                    reverse=is_desc,
                )
            elif sort_col == "current_return":
                accounts_map[acc_id].sort(key=get_exit_ret, reverse=is_desc)
            elif sort_col == "shadow_hwm":
                accounts_map[acc_id].sort(
                    key=lambda s: s.get("shadow_hwm", -999.0), reverse=is_desc
                )
            elif sort_col == "shadow":
                accounts_map[acc_id].sort(
                    key=lambda s: (
                        s.get("current_return") if s.get("current_return") is not None else -999.0
                    ),
                    reverse=is_desc,
                )
            else:  # name
                accounts_map[acc_id].sort(
                    key=lambda s: (s.get("name") or s.get("id", "")).lower(), reverse=is_desc
                )

        # Build symphonies list for M1 analytics helpers from bot_state.
        # Fields derived: last_percent_change from current_return/100, value from current_value.
        # Composer CR/MDD fields use None default so missing data is distinguishable from 0.0.
        today_str = datetime.now().strftime("%Y-%m-%d")
        symphonies_list = []
        for k in symphony_keys:
            s = state_data[k]
            cr = s.get("current_return") or 0.0
            val = s.get("current_value") or 0.0
            symphonies_list.append(
                {
                    "id": k,
                    "value": val,
                    "last_percent_change": cr / 100.0,
                    "simple_return": s.get("simple_return"),
                    "net_deposits": s.get("net_deposits"),
                    "time_weighted_return": s.get("time_weighted_return"),
                    "max_drawdown": s.get("max_drawdown"),
                    "trading_day": today_str,
                }
            )

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
                s["_cr"] = analytics.get_symphony_cumulative_return(
                    sym_dict, s, trading_day=_today_et
                )
            except (KeyError, TypeError, ValueError):
                s["_cr"] = {"if_held": None, "dry_run": None}
            try:
                s["_mdd"] = analytics.get_symphony_max_drawdown(sym_dict, s, trading_day=_today_et)
            except (KeyError, TypeError, ValueError):
                s["_mdd"] = {"if_held": None, "dry_run": None}
            # Additive: parabolic velocity (current_return − prev_return, percent units).
            # prev_return is stored by the engine each cycle; None when symphony is new.
            _cr_now = s.get("current_return")
            _cr_prev = s.get("prev_return")
            s["para_velocity"] = (
                round(float(_cr_now) - float(_cr_prev), 6)
                if _cr_now is not None and _cr_prev is not None
                else None
            )

        portfolio_strip = _compute_portfolio_strip(state_data, trading_day=_today_et)

        data_as_of = datetime.now().strftime("%H:%M ET")

        try:
            rendered_html = render_template(
                "table_partial.html",
                accounts_map=accounts_map,
                account_labels=account_labels,
                sort_col=sort_col,
                sort_dir=sort_dir,
                data_as_of=data_as_of,
            )
        except Exception:
            rendered_html = "<table><tbody></tbody></table>"

        # PA-M1F-14: shadow_divergence — one lightweight GROUP BY query, not on execution path.
        try:
            shadow_divergence = database.get_shadow_divergence(today_str)
        except Exception:
            shadow_divergence = {"by_symphony": {}, "portfolio_today": None}
        for sym_id, entry in shadow_divergence["by_symphony"].items():
            entry["name"] = (state_data.get(sym_id) or {}).get("name") or sym_id

        _alert_row = database.read_fleet_alert()
        _alert = (
            _alert_row
            if (_alert_row is not None and _alert_row.get("dismissed_at_et") is None)
            else None
        )

        # Inject guard_alpha into triggered symphony entries for dashboard card verdicts.
        try:
            _triggered_ids = [
                k for k, v in state_data.items()
                if isinstance(v, dict) and v.get("triggered")
            ]
            if _triggered_ids:
                _ga_map = database.get_guard_alpha_by_symphony(_triggered_ids)
                for _sid, _ga in _ga_map.items():
                    if _sid in state_data and isinstance(state_data[_sid], dict):
                        _cr = state_data[_sid].get("current_return") or 0.0
                        state_data[_sid].setdefault("guard_alpha", round(float(_ga) - float(_cr), 6))
        except Exception:
            pass

        # Build per-symphony list for card live-refresh (cards-live).
        # Reuses _tc/_cr/_mdd already attached above — no re-computation.
        # Status priority: triggered > para_armed > tp_armed > armed > standby.
        def _sym_status(s: dict) -> str:
            if s.get("triggered"):
                return "triggered"
            if s.get("para_armed"):
                return "para_armed"
            if s.get("tp_armed"):
                return "tp_armed"
            if s.get("armed"):
                return "armed"
            return "standby"

        def _tc_cr_mdd_floats(s: dict) -> tuple:
            tc = s.get("_tc") or {}
            cr = s.get("_cr") or {}
            mdd = s.get("_mdd") or {}
            tc_bot  = (tc.get("dry_run")  if isinstance(tc,  dict) else tc)  or None
            tc_held = (tc.get("if_held")  if isinstance(tc,  dict) else None) or None
            cr_bot  = (cr.get("dry_run")  if isinstance(cr,  dict) else cr)  or None
            cr_held = (cr.get("if_held")  if isinstance(cr,  dict) else None) or None
            mdd_bot  = (mdd.get("dry_run") if isinstance(mdd, dict) else mdd) or None
            mdd_held = (mdd.get("if_held") if isinstance(mdd, dict) else None) or None
            return tc_bot, tc_held, cr_bot, cr_held, mdd_bot, mdd_held

        _symphonies_for_cards: list[dict] = []
        for _k in symphony_keys:
            _s = state_data[_k]
            _tc_b, _tc_h, _cr_b, _cr_h, _mdd_b, _mdd_h = _tc_cr_mdd_floats(_s)
            _symphonies_for_cards.append({
                "id": _k,
                "status": _sym_status(_s),
                # Raw bot_state fields needed for card status pill + MC dial refresh.
                "current_return": _s.get("current_return"),
                "stop_trigger": _s.get("stop_trigger"),
                "mc_prob": _s.get("mc_prob"),
                "armed": bool(_s.get("armed")),
                "tp_armed": bool(_s.get("tp_armed")),
                "para_armed": bool(_s.get("para_armed")),
                "triggered": bool(_s.get("triggered")),
                "triggered_reason": _s.get("triggered_reason"),
                # guard_alpha for the outcome banner on triggered cards.
                # Injected above from database.get_guard_alpha_by_symphony().
                "guard_alpha": _s.get("guard_alpha"),
                # Analytics-derived display values.
                "tc_bot":  _tc_b,
                "tc_held": _tc_h,
                "cr_bot":  _cr_b,
                "cr_held": _cr_h,
                "mdd_bot":  _mdd_b,
                "mdd_held": _mdd_h,
            })

        return jsonify(
            {
                "status": "active",
                "market_state": market_state,
                "frozen_at": None,
                "state": state_data,
                "bot_state": state_data,
                "live_mode": live_mode,
                "execution_start_time": env_vars.get("EXECUTION_START_TIME", "09:30"),
                "next_run_seconds": next_run_seconds,
                "html": rendered_html,
                "portfolio_strip": portfolio_strip,
                "data_as_of": data_as_of,
                "last_successful_cycle_at": state_data.get("last_successful_cycle_at"),
                "shadow_divergence": shadow_divergence,
                "fleet_correlation_alert": _alert,
                "symphonies": _symphonies_for_cards,
                "meta": _build_meta(
                    state_data,
                    next_run_seconds=next_run_seconds,
                    market_state=market_state,
                    portfolio_strip=_injected_portfolio_strip or portfolio_strip,
                ),
                **_additive,
            }
        )
    except Exception as e:
        _daemon_log.error("api route failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500
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
        _daemon_log.error("api_symphony_logs failed for %s: %s", symphony_id, e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500
    finally:
        _ro_conn.close()


@app.route("/api/hero-chart/<window>")
def get_hero_chart(window):
    """Return hist_dates/hist_bot/hist_held for the requested time window.

    window values: 30d, 60d, 90d, 125d, ytd, 1y
    Fetches from shadow_history with an appropriate days parameter so each
    window returns a distinct, correctly-sized slice.
    """
    now = datetime.now(_ET)
    if window == "ytd":
        jan1 = datetime(now.year, 1, 1).date()
        days_since_jan1 = max((now.date() - jan1).days, 1)
        fetch_days = min(days_since_jan1 + 30, 365)
    elif window == "1y":
        fetch_days = 365
    elif window == "125d":
        fetch_days = 125
    elif window == "90d":
        fetch_days = 90
    elif window == "60d":
        fetch_days = 60
    else:
        fetch_days = 30

    # Minimum trading days needed for the window to be meaningful
    _min_days = {"30d": 20, "60d": 40, "90d": 60, "125d": 80, "ytd": 10, "1y": 100}
    required = _min_days.get(window, 10)

    try:
        shadow_result = analytics.get_portfolio_daily_returns_from_shadow(days=fetch_days)
        if shadow_result is not None:
            dates, daily = shadow_result
            running = 1.0
            bot_series = []
            for d in daily:
                running *= 1.0 + d / 100.0
                bot_series.append(round((running - 1.0) * 100.0, 4))
            if window == "ytd":
                jan1_str = str(datetime(now.year, 1, 1).date())
                idx = 0
                while idx < len(dates) and dates[idx] < jan1_str:
                    idx += 1
                dates = dates[idx:]
                bot_series = bot_series[idx:]
            insufficient = len(dates) < required
            return jsonify({
                "hist_dates": dates,
                "hist_bot": bot_series,
                "hist_held": bot_series,
                "window": window,
                "source": "shadow_history",
                "insufficient_history": insufficient,
                "available_days": len(dates),
            })
    except Exception:
        pass

    return jsonify({
        "hist_dates": [], "hist_bot": [], "hist_held": [],
        "window": window,
        "insufficient_history": True,
        "available_days": 0,
    })


@app.route("/api/chart/<symphony_id>")
def get_chart_data(symphony_id):
    _ro_conn = database.get_ro_connection()
    try:
        chart_data = database.load_chart_history()
        symphony_data = chart_data.get("symphonies", {}).get(symphony_id, [])
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = _ro_conn.execute(
                "SELECT substr(ts_et, 12, 5) AS hhmm, shadow_return, current_return "
                "FROM shadow_history "
                "WHERE symphony_id = ? AND trading_day = ? "
                "ORDER BY ts_utc",
                (symphony_id, today_str),
            ).fetchall()
            held_by_time = {r[0]: r[1] for r in rows}
            ret_by_time  = {r[0]: r[2] for r in rows}
        except Exception:
            held_by_time = {}
            ret_by_time  = {}

        if symphony_data:
            if held_by_time:
                symphony_data = [
                    {**pt, "held": held_by_time.get(pt.get("time"))}
                    for pt in symphony_data
                ]
        else:
            # chart_history empty — build intraday tape from shadow_history (B-03)
            symphony_data = [
                {"time": t, "return": ret_by_time.get(t), "held": held_by_time.get(t)}
                for t in sorted(held_by_time.keys())
            ]

        return jsonify({"status": "success", "data": symphony_data})
    except Exception as e:
        _daemon_log.error("get_chart_data failed for %s: %s", symphony_id, e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500
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
        _daemon_log.error("api_triggers failed: %s", e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500
    finally:
        _ro_conn.close()


@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    # Side-effect ban: write_fleet_alert must not block the request thread.
    # Submit to the module-level executor so the handler returns immediately
    # while the dismissed_at_et write still lands durably in fleet_alert_state.
    def _dismiss_async():
        try:
            row = database.read_fleet_alert()
            if row is not None:
                row["dismissed_at_et"] = datetime.now(_ET).isoformat()
                database.write_fleet_alert(row)
        except Exception:
            logging.error("fleet_alert_dismiss: background write failed", exc_info=True)

    _DISMISS_EXECUTOR.submit(_dismiss_async)
    return jsonify({"status": "ok"})


@app.route("/api/trigger", methods=["POST"])
def manual_trigger():
    # Dashboard side-effect ban: routes must not spawn the engine (arch constraint 2).
    # The scheduler is the only legal engine spawner.
    return jsonify({"status": "success", "message": "Manual trigger disabled — use the scheduler."})


@app.route("/api/accounts")
def list_accounts():
    """Return available Composer account UUIDs and labels (B-08 workspace switcher)."""
    env_vars = _dotenv_module.dotenv_values(ENV_FILE_PATH)
    pairs = [
        (env_vars.get("ACCOUNT_INDIVIDUAL", "").strip(), "Individual"),
        (env_vars.get("ACCOUNT_ROTH", "").strip(), "Roth IRA"),
        (env_vars.get("ACCOUNT_TRAD", "").strip(), "Trad. IRA"),
    ]
    accounts = [
        {"uuid": uuid, "label": label}
        for uuid, label in pairs
        if uuid
    ]
    return jsonify({"accounts": accounts})


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
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] Forcing EOD Analysis for {prev_date_str}..."
            )
            import autotuner
            import reporting

            reporting.generate_eod_snapshot(
                bot_state,
                prev_date_str,
                is_post_rebalance=False,
                discord_webhook_url=discord_webhook,
            )
            reporting.generate_eod_snapshot(
                bot_state,
                prev_date_str,
                is_post_rebalance=True,
                discord_webhook_url=discord_webhook,
            )
            autotuner_changes = autotuner.run_autotuner(
                bot_state, prev_date_str, account_uuids, is_forced=True,
                spec_bundle_id=database.get_or_create_phase1_theory_bundle_id(),
            )
            reporting.send_eod_discord_post(
                prev_date_str,
                os.path.join(analytics._POST_MORTEMS_DIR, f"post_mortem_{prev_date_str}.json"),
                autotuner_changes,
                discord_webhook,
            )
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Forced EOD Analysis complete.")

        threading.Thread(target=run_eod_tasks, daemon=True).start()
        return jsonify(
            {"status": "success", "message": "EOD Analysis initiated for " + prev_date_str}
        )
    except Exception as e:
        _daemon_log.error("force_eod failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


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
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] Resending Discord Report for {prev_date_str}..."
            )
            import reporting

            # Pass None for optimization_results to skip tuning and just send the current JSON
            reporting.send_eod_discord_post(
                prev_date_str, f"post_mortem_{prev_date_str}.json", None, discord_webhook
            )
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Discord resend complete.")

        threading.Thread(target=run_discord_push, daemon=True).start()
        return jsonify(
            {"status": "success", "message": "Discord push initiated for " + prev_date_str}
        )
    except Exception as e:
        _daemon_log.error("resend_discord failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


@app.route("/api/history/<int:days>")
def get_history(days):
    stats = analytics.get_history_summary(days=days)
    stats["window_days"] = days
    return jsonify(stats)


# --- 2b. History Tab ---
@app.route("/history")
def history_page():
    """Render the Guard Alpha History tab (read-only operator surface)."""
    return render_template("history.html", active_route="history", meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))))


# --- 2c. Performance Tab (DV2) ---
@app.route("/performance")
def performance_page():
    """Render the Performance tab (read-only operator surface).

    Pure render — no database mutation, no engine invocation, no network I/O.
    Client-side JS pulls /api/performance and /api/performance/symphonies on
    load and on scope/symphony changes.
    """
    return render_template(
        "performance.html",
        min_history_days=_PERFORMANCE_MIN_HISTORY_DAYS,
        active_route="performance",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
    )


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
          "live_metrics":   {8 documented keys — Phase 2 adds 'volatility'},
          "shadow_metrics": {8 documented keys — Phase 2 adds 'volatility'},
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
        return jsonify(
            {
                "status": "error",
                "message": (
                    f"invalid scope {scope!r}; expected one of {list(_PERFORMANCE_VALID_SCOPES)}"
                ),
            }
        ), 400

    try:
        days = int(request.args.get("days", 60))
    except (TypeError, ValueError):
        return jsonify(
            {
                "status": "error",
                "message": "days must be an integer",
            }
        ), 400

    symphony_id = request.args.get("symphony_id")
    if scope == "symphony" and not symphony_id:
        return jsonify(
            {
                "status": "error",
                "message": "symphony_id is required when scope=symphony",
            }
        ), 400

    history = analytics.get_history_with_cache_invalidation(
        days=days, base_dir=analytics._POST_MORTEMS_DIR
    )

    if scope == "aggregate":
        dates, live_returns, shadow_returns = analytics.compute_aggregate_returns(history)
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

    return jsonify(
        {
            "scope": scope,
            "dates": list(dates),
            "live_returns": live_returns_out,
            "shadow_returns": shadow_returns_out,
            "live_metrics": live_metrics,
            "shadow_metrics": shadow_metrics,
            "observation_count": observation_count,
            "insufficient_history": insufficient_history,
            "window_days": days,
        }
    )


@app.route("/api/performance/symphonies")
def api_performance_symphonies():
    """Sorted list of symphony_ids present in the post-mortem history."""
    history = analytics.get_history_with_cache_invalidation(
        base_dir=analytics._POST_MORTEMS_DIR
    )
    symphonies = analytics.list_available_symphonies(history)
    return jsonify({"symphonies": list(symphonies)})


# --- 3. Account Liquidation ---
def perform_account_liquidation(account_id, key, secret, live_mode):
    headers = {
        "x-api-key-id": key,
        "authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
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
        return jsonify(
            {"status": "error", "message": "confirm_account_id does not match account_id"}
        ), 400
    if not confirm_phrase:
        return jsonify({"status": "error", "message": "confirm_phrase is required"}), 400
    if confirm_phrase != "LIQUIDATE":
        return jsonify(
            {"status": "error", "message": "confirm_phrase must be exactly LIQUIDATE"}
        ), 400

    env_vars = dotenv_values(".env")
    live_mode = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

    if not (account_id and env_vars.get("COMPOSER_KEY_ID")):
        return jsonify({"status": "error", "message": "Missing credentials or account ID."}), 400

    ts_et = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")

    # Audit: Discord alert + ERROR log on every invocation regardless of live_mode
    discord_url = env_vars.get("DISCORD_WEBHOOK_URL", "")
    if discord_url:
        try:
            requests.post(
                discord_url,
                json={
                    "content": f"EMERGENCY LIQUIDATION TRIGGERED on {account_id} at {ts_et} ET (live={live_mode})"
                },
                timeout=5,
            )
        except Exception:
            pass

    _daemon_log.error(
        "EMERGENCY LIQUIDATION TRIGGERED on %s at %s ET (live=%s)",
        account_id,
        ts_et,
        live_mode,
    )

    if not live_mode:
        # Real-money safety gate: never spawn the liquidation thread when
        # LIVE_EXECUTION is False.  Return an explicit dry-run signal so the
        # operator dashboard can distinguish a successful no-op from a real
        # execution.
        return jsonify(
            {
                "status": "dry_run",
                "dry_run": True,
                "message": "Panic-stop disabled in non-LIVE mode. Set LIVE_EXECUTION=True to arm.",
                "live_mode": False,
                "executed": False,
            }
        )

    threading.Thread(
        target=perform_account_liquidation,
        args=(
            account_id,
            env_vars.get("COMPOSER_KEY_ID"),
            env_vars.get("COMPOSER_SECRET"),
            live_mode,
        ),
    ).start()
    return jsonify(
        {
            "status": "success",
            "message": "Liquidation initiated.",
            "live_mode": True,
            "executed": True,
        }
    )


# --- 3b. Settings Page Route ---
@app.route("/settings")
def settings_page():
    """Render the Settings page (read-only template; mutations via /api/settings)."""
    return render_template("settings.html", active_route="settings", meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))))


# --- 4. Tabbed Settings / Control Panel Routes ---

# Keys whose values must never be echoed to the browser in GET /api/settings.
# Includes API credentials, webhook URLs, and account UUIDs (Alpaca account
# identifiers are sensitive — exposing them aids account enumeration attacks).
_MASKED_SETTINGS_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "COMPOSER_KEY_ID",
        "COMPOSER_SECRET",
        "ALPACA_KEY",
        "ALPACA_SECRET",
        "DISCORD_WEBHOOK_URL",
        "ACCOUNT_INDIVIDUAL",
        "ACCOUNT_ROTH",
        "ACCOUNT_TRAD",
    }
)


def _mask_secret(value: str | None) -> str:
    """Return '' for any secret key — raw values must never reach the browser."""
    return ""


# Algorithm parameter metadata returned by GET /api/settings.
# Drives the Algorithm parameters section of the Settings screen.
_ALGO_PARAM_META = {
    "TRIGGER_THRESHOLD_PCT": {
        "help": "Monte Carlo threshold for arming the trailing stop. Lower = bot waits longer before defending.",
        "unit": "%",
        "kind": "pct",
    },
    "TAKE_PROFIT_MC_PCT": {
        "help": "Aggressive take-profit threshold once MC probability collapses.",
        "unit": "%",
        "kind": "pct",
    },
    "MAX_SQUEEZE_FLOOR": {
        "help": "Tightest the stop distance can shrink under the log-time squeeze.",
        "unit": "×",
        "kind": "mult",
    },
    "VWAP_CROSS_HWM_PCT": {
        "help": "Return needed to activate the VWAP Breakdown defense (System A).",
        "unit": "%",
        "kind": "pct",
    },
    "VWAP_BLEED_MULTIPLIER": {
        "help": "Multiplier on 20d vol used to set the VWAP Bleed Cut threshold.",
        "unit": "×",
        "kind": "mult",
    },
    "VWAP_BLEED_TICKS": {
        "help": "Consecutive ticks required below bleed threshold to trigger.",
        "unit": "ticks",
        "kind": "int",
    },
    "PARABOLIC_VELOCITY_THRESHOLD": {
        "help": "Tick-velocity threshold that arms the parabolic squeeze ratchet.",
        "unit": "",
        "kind": "decimal",
    },
    "MAX_PARABOLIC_SQUEEZE": {
        "help": "Stop tightening once parabolic squeeze is armed or breakeven locked.",
        "unit": "×",
        "kind": "mult",
    },
}


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Returns Globals from .env and Symphony Strategies from SQLite."""
    env_vars = dotenv_values(ENV_FILE_PATH)
    globals_data = {
        "LIVE_EXECUTION": env_vars.get("LIVE_EXECUTION", "False"),
        "EXECUTION_START_TIME": env_vars.get("EXECUTION_START_TIME", "09:30"),
        "EXIT_AUTHORITY": env_vars.get("EXIT_AUTHORITY", "per_symphony"),
    }
    # Populate algorithm param globals from .env, falling back to database defaults.
    for key in _ALGO_PARAM_META:
        if key not in globals_data:
            globals_data[key] = env_vars.get(key, str(database.DEFAULT_STRATEGY.get(key, "")))

    # Build `secrets` dict — masked placeholder per credential key.
    # Also kept in globals for backward compatibility with existing callers.
    # The frontend credentials panel renders from `secrets`; `globals` retains the
    # masked values so existing consumers of globals["COMPOSER_KEY_ID"] etc. still work.
    secrets_data = {key: _mask_secret(env_vars.get(key)) for key in _MASKED_SETTINGS_KEYS}
    # Merge masked secrets into globals (preserves prior contract).
    globals_data.update(secrets_data)

    # Fetch unique symphony names from the current bot_state
    state_data = database.load_state()
    symphony_names = set()
    raw_names = {}  # normalized_name -> display name
    for data in state_data.values():
        if isinstance(data, dict) and "name" in data:
            norm = database.normalize_name(data["name"])
            symphony_names.add(norm)
            raw_names[norm] = data["name"]

    symphonies_data = {}
    for name in symphony_names:
        symphonies_data[name] = database.get_symphony_strategy(name)

    # Build symphonies_list for the Settings UI (id + display name).
    symphonies_list = [
        {"id": norm, "name": raw_names.get(norm, norm)}
        for norm in sorted(symphony_names)
    ]

    # symphony_overrides mirrors symphonies_data but is the canonical field name
    # expected by settings.js for the override editor pane.
    return jsonify({
        "globals": globals_data,
        "secrets": secrets_data,
        "symphonies": symphonies_data,
        "symphony_overrides": symphonies_data,
        "symphonies_list": symphonies_list,
        "param_meta": _ALGO_PARAM_META,
    })


# Allowlist of global keys that the operator dashboard may write to .env (A-1).
# LIVE_EXECUTION is deliberately excluded — arming real-money execution must
# never be possible via an unauthenticated dashboard POST.  Credential/webhook
# keys (_MASKED_SETTINGS_KEYS) are also excluded; rotate credentials directly
# in .env, never through the dashboard.
_SETTINGS_WRITE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "EXECUTION_START_TIME",
        "EXIT_AUTHORITY",
    }
    | set(_ALGO_PARAM_META.keys())
)


@app.route("/api/settings", methods=["POST"])
def save_settings():
    """Saves allowlisted globals to .env and symphony strategies to SQLite.

    Rejects any key not in _SETTINGS_WRITE_ALLOWLIST (including LIVE_EXECUTION
    and all credential keys) with a 400 so the client gets an actionable error
    rather than a silent no-op.
    """
    payload = request.json

    try:
        # Save Globals — allowlist enforced (A-1).
        globals_payload = payload.get("globals", {})
        rejected = [k for k in globals_payload if k not in _SETTINGS_WRITE_ALLOWLIST]
        if rejected:
            return jsonify({
                "status": "error",
                "message": f"Rejected keys not in settings allowlist: {sorted(rejected)}",
            }), 400

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
        _daemon_log.error("save_settings failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


@app.route("/api/symphony-settings/<symphony_name>", methods=["GET"])
def get_symphony_settings(symphony_name: str):
    """Return per-symphony modal state: live_mode, global_live, parameters, locked_vars, advisor observations.

    Consumes the read-only DB path for parameters/locked_vars/live_mode and
    the env for the global master-switch flag.  Never writes; never reruns the engine.
    """
    symphony_name = database.normalize_name(symphony_name)
    # Use module-level dotenv_values so test patches on app_module.dotenv_values take effect
    # (same convention as save_settings and other dashboard routes).
    env_vars = dotenv_values(ENV_FILE_PATH)
    global_live = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")

    strategy = database.get_symphony_strategy(symphony_name)
    # get_symphony_strategy defaults live_mode=False (arch rule 4: explicit, never by omission).
    live_mode = bool(strategy.get("live_mode", False))

    # advisor_observations.symphony_id is keyed by normalize_name(sym["name"])
    # (written by autotuner.py:save_autotune_run(symphony_id=normalized_name)).
    # The URL param (symphony_name) arrives as a Composer hash like 'iaSOOUsmnCJHiZvbrWfs'
    # normalized to its lowercase form — never the human name.  Resolve hash → name
    # via bot_state before querying, falling back to symphony_name if no match found.
    bot_state = database.load_state()
    obs_symphony_id = symphony_name  # fallback: pass as-is (may yield 0 rows on hash)
    for sym_key, sym_data in bot_state.items():
        if not isinstance(sym_data, dict) or "name" not in sym_data:
            continue
        normalized_sym_name = database.normalize_name(sym_data["name"])
        # Match on normalized Composer hash (the URL param) OR on the normalized name
        # (handles the rare case where the name is passed directly instead of the hash).
        if database.normalize_name(sym_key) == symphony_name or normalized_sym_name == symphony_name:
            obs_symphony_id = normalized_sym_name
            break
    advisor_observations = database.get_advisor_observations_for_symphony(obs_symphony_id)

    return jsonify({
        "live_mode": live_mode,
        "global_live": global_live,
        "parameters": strategy.get("params", {}),
        "locked_vars": strategy.get("locked_vars", []),
        "advisor_observations": advisor_observations,
    })


@app.route("/api/symphony-settings/<symphony_name>", methods=["POST"])
def save_symphony_settings(symphony_name: str):
    """Persist per-symphony live_mode and locked_vars changes.

    Rules enforced here (arch safety layer — CSRF is enforced by @before_request):
      - live_mode=True requires confirm=True in the payload; without it the request
        is rejected with 400 (bare toggle-click must never persist live, AC-3).
      - live_mode changes go through set_symphony_live_mode (writes config_audit_log, AC-9).
      - locked_vars changes go through save_symphony_strategy (preserves params, AC-6).
      - LIVE_EXECUTION (global master-switch) is never touched here (arch rule 4 / AC-10).
    """
    _validate_csrf()
    symphony_name = database.normalize_name(symphony_name)
    payload = request.json or {}

    try:
        # AC-3: live_mode must be a boolean or integer 0/1 only.
        # Reject strings (would silently bypass the confirm gate) and out-of-range
        # integers like 2 (ambiguous — not a recognised live/dry value).
        live_mode_raw = payload.get("live_mode")
        if live_mode_raw is not None:
            if not isinstance(live_mode_raw, (bool, int)):
                return jsonify({
                    "status": "error",
                    "message": "live_mode must be a boolean (true/false), not a string or other type.",
                }), 400
            # isinstance(True, int) is True in Python so bool is already covered above;
            # for bare ints, only 0 and 1 are valid — reject out-of-range values.
            if not isinstance(live_mode_raw, bool) and live_mode_raw not in (0, 1):
                return jsonify({
                    "status": "error",
                    "message": "live_mode integer must be 0 or 1.",
                }), 400

        if live_mode_raw is True or live_mode_raw == 1:
            if not payload.get("confirm"):
                return jsonify({
                    "status": "error",
                    "message": (
                        "confirm=true is required to enable live trading. "
                        "A bare toggle click must never persist live mode."
                    ),
                }), 400
            database.set_symphony_live_mode(symphony_name, 1, "dashboard")
        elif live_mode_raw is False or live_mode_raw == 0:
            # OFF toggle is immediate — no confirm required (AC-3).
            database.set_symphony_live_mode(symphony_name, 0, "dashboard")

        # AC-6: locked_vars changes persisted via save_symphony_strategy.
        if "locked_vars" in payload:
            strategy = database.get_symphony_strategy(symphony_name)
            database.save_symphony_strategy(
                symphony_name,
                strategy.get("params", {}),
                payload["locked_vars"],
            )

        return jsonify({"status": "success"})
    except Exception as e:
        _daemon_log.error("save_symphony_settings failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


# The 11 real post_mortem dates produced by the live engine.
# Everything else in post_mortems/ is synthetic backfill and must be flushed.
# Verified 2026-05-21 against seed agent report + filesystem audit.
_REAL_POST_MORTEM_DATES = frozenset({
    "2025-05-12", "2025-05-14", "2025-05-16",
    "2026-05-11",
    "2026-05-14", "2026-05-15", "2026-05-16",
    "2026-05-17", "2026-05-18", "2026-05-19", "2026-05-20",
})


@app.route("/api/settings/flush-resync", methods=["POST"])
def flush_resync():
    """Delete synthetic post_mortem backfill files, reset per-symphony bot_state, resync Composer.

    Phase 1 — File purge: scans analytics._POST_MORTEMS_DIR, removes files whose dates are
      NOT in _REAL_POST_MORTEM_DATES, keeps real ones.  Invalidates the analytics cache.
    Phase 2 — Symphony state reset (background): loads bot_state, enumerates per-symphony
      entries (isinstance(v, dict) and "name" in v), strips runtime tracking fields (stop
      levels, returns, trigger flags), preserves "name" and "account".  Read happens on the
      request thread for the response; write is dispatched to _DISMISS_EXECUTOR so the
      handler returns immediately.  Exceptions are logged, not propagated.
    Phase 3 — Composer resync: triggers a background account-totals refresh.

    Safe + idempotent: file phase only removes synthetic dates; state phase only strips
    runtime tracking fields (stop levels, returns, trigger flags) — identity keys are kept.
    """
    import re as _re
    _pm_date_re = _re.compile(r"post_mortem_(\d{4}-\d{2}-\d{2})\.json$")

    deleted: list[str] = []
    kept: list[str] = []
    errors: list[str] = []

    # --- Phase 1: post-mortem file purge ---
    pm_dir = analytics._POST_MORTEMS_DIR
    os.makedirs(pm_dir, exist_ok=True)
    for fname in sorted(os.listdir(pm_dir)):
        m = _pm_date_re.match(fname)
        if not m:
            continue
        date_str = m.group(1)
        fpath = os.path.join(pm_dir, fname)
        if date_str in _REAL_POST_MORTEM_DATES:
            kept.append(fname)
        else:
            try:
                os.remove(fpath)
                deleted.append(fname)
            except OSError as exc:
                errors.append(f"{fname}: {exc}")
                _daemon_log.error("flush_resync: failed to delete %s: %s", fpath, exc)

    # Invalidate the analytics post_mortem cache so the next request reloads from real files.
    analytics._HISTORY_CACHE = {"key": None, "data": None}

    # --- Phase 2: enumerate per-symphony bot_state entries; dispatch reset to background thread ---
    # Dashboard side-effect ban: save_state must not block the request thread.
    # We read current state here to enumerate symphony names for the response, then
    # submit the actual write (strip tracking fields, preserve name+account) to
    # _DISMISS_EXECUTOR so the handler returns immediately.  Same pattern as
    # fleet_alert_dismiss / _DISMISS_EXECUTOR.
    symphonies_reset: list[str] = []
    try:
        _state = database.load_state()
        for _sym_id, _sym_val in list(_state.items()):
            if not (isinstance(_sym_val, dict) and "name" in _sym_val):
                continue
            symphonies_reset.append(_sym_val.get("name", _sym_id))
        _daemon_log.info("flush_resync: identified %d symphony state entries for reset", len(symphonies_reset))
    except Exception as exc:
        _daemon_log.error("flush_resync: symphony state read failed: %s", exc)
        errors.append(f"state_read: {exc}")

    def _flush_state_async():
        try:
            with _FLUSH_STATE_LOCK:
                state = database.load_state()
                # LATENT-001: count resets from this thread's own iteration, not
                # the closed-over request-thread list (which may differ if the
                # engine wrote between the two load_state calls).
                _reset_count = 0
                for sym_id, sym_val in list(state.items()):
                    if not (isinstance(sym_val, dict) and "name" in sym_val):
                        continue
                    display_name = sym_val.get("name", sym_id)
                    account = sym_val.get("account")
                    state[sym_id] = {"name": display_name}
                    if account is not None:
                        state[sym_id]["account"] = account
                    _reset_count += 1
                database.save_state(state)
            _daemon_log.info("flush_resync: background reset wrote %d symphony state entries", _reset_count)
        except Exception:
            logging.error("flush_resync: background state reset failed", exc_info=True)

    _DISMISS_EXECUTOR.submit(_flush_state_async)

    # --- Phase 3: Composer resync ---
    resync_ok = False
    try:
        t = threading.Thread(target=_refresh_account_totals, daemon=True)
        t.start()
        t.join(timeout=15.0)
        resync_ok = "portfolio_cr" in _account_totals_cache
    except Exception as exc:
        _daemon_log.error("flush_resync: Composer resync failed: %s", exc)
        errors.append(f"resync: {exc}")

    _daemon_log.info(
        "flush_resync: deleted %d synthetic files, kept %d real, reset %d symphonies, resync_ok=%s, errors=%d",
        len(deleted), len(kept), len(symphonies_reset), resync_ok, len(errors),
    )

    return jsonify({
        "status": "ok" if not errors else "partial",
        "deleted_count": len(deleted),
        "kept_count": len(kept),
        "deleted": deleted,
        "kept": kept,
        "symphonies_reset": symphonies_reset,
        "symphonies_reset_count": len(symphonies_reset),
        "composer_resync": resync_ok,
        "errors": errors,
    })


# --- 5. AI Advisor Routes ---
@app.route("/ai-advisor", methods=["GET"])
def ai_advisor_tab():
    """Render the Claude AI Config Advisor tab.

    Passes the last _ADVISOR_OBSERVATIONS_PAGE_LIMIT advisor observations
    to the template for the observations section.  Observations are fetched
    across all known advisor roles using read-only accessors.
    """
    observations: list[dict] = []
    for role in _ADVISOR_ROLES:
        observations.extend(
            database.get_advisor_observations_for_role(
                role, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
            )
        )
    # Deduplicate by id and apply page limit
    seen: set = set()
    deduped_obs: list[dict] = []
    for obs in observations:
        if obs["id"] not in seen:
            seen.add(obs["id"])
            deduped_obs.append(obs)
    # Suppress pure feature-off stubs: NOT_APPLICABLE rows whose raw_response
    # carries {"feature_flag": "off"} are audit-trail bookkeeping, not advice.
    # Showing them in the recommendations table misleads the operator.
    # The Divergence Explainer writes these when §B is disabled (the current
    # default — CVaR divergence detection is deferred per council verdict).
    deduped_obs = [
        obs for obs in deduped_obs
        if not (
            obs.get("verdict") == "NOT_APPLICABLE"
            and isinstance(obs.get("raw_response"), dict)
            and obs["raw_response"].get("feature_flag") == "off"
        )
    ]
    observations = deduped_obs[: _ADVISOR_OBSERVATIONS_PAGE_LIMIT]

    return render_template(
        "ai_advisor.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        observations=observations,
    )


@app.route("/ai-advisor/correlations", methods=["GET"])
def ai_advisor_correlations():
    """Render the M1 Correlations diagnostic tab (AC-1.1..1.4).

    Pure measurement — no backtest, no gate, no write path.  The route
    imports advisors.correlation_diagnostic lazily (inside the function)
    so the module stays off the live execution path and sys.modules
    injection in tests can intercept the import at call time (AC-X2).

    Template context:
      correlation_matrix  — list of PairResult objects from compute_pairwise_correlations
      as_of               — ISO timestamp string for the operator's reference (AC-1.2)
      crisis_caveat       — always-on instability warning string (AC-1.4)
      insufficient_data   — True when no pairs are available (AC-1.3)
    """
    # Lazy import keeps the module off the live 1-minute execution path (AC-X2).
    from advisors import correlation_diagnostic  # noqa: PLC0415

    # Build per-symphony return series from post-mortem history (read-only).
    history = analytics.get_history_with_cache_invalidation(
        base_dir=analytics._POST_MORTEMS_DIR
    )
    sym_ids = analytics.list_available_symphonies(history)
    series_dict: dict[str, list[float]] = {}
    for sym_id in sym_ids:
        ret_series = analytics.compute_per_symphony_returns(history, sym_id)
        if ret_series:
            series_dict[sym_id] = ret_series

    # Run the pure correlation diagnostic — no DB writes, no engine calls.
    matrix = correlation_diagnostic.compute_pairwise_correlations(series_dict)

    # AC-1.4: the crisis-instability caveat is mandatory and always surfaced.
    # Single source of truth lives in the module that owns the concept.
    crisis_caveat = correlation_diagnostic.CRISIS_CAVEAT

    return render_template(
        "ai_advisor_correlations.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        correlation_matrix=matrix,
        as_of=datetime.now(_ET).isoformat(),
        crisis_caveat=crisis_caveat,
        insufficient_data=len(matrix) == 0,
    )


@app.route("/ai-advisor/asset-swaps", methods=["GET"])
def ai_advisor_asset_swaps():
    """Render the M3 Asset Swaps tab (AC-2.1..2.5, AC-X1..X5).

    Read-only surface — no writes to live positions, no Composer write endpoints.
    All swap proposals are advisory-only (apply manually in Composer).

    Template context:
      no_api_key        — True when Composer credentials are absent (AC-X4)
      symphonies        — list of known symphony IDs for the operator-initiated form
    """
    # Lazy import keeps the module off the live 1-minute execution path (AC-X2).
    from advisors.asset_swap_engine import _has_composer_key  # noqa: PLC0415

    no_api_key = not _has_composer_key()

    # Provide the symphony list for the operator-initiated "Try a swap" form.
    # Reads from the same performance API endpoint the existing advisor tab uses.
    symphonies: list[str] = []
    try:
        import analytics as _analytics  # noqa: PLC0415
        history = _analytics.get_history_with_cache_invalidation(
            base_dir=_analytics._POST_MORTEMS_DIR
        )
        symphonies = _analytics.list_available_symphonies(history)
    except Exception:
        pass  # Symphony list is optional; the form still renders without it.

    return render_template(
        "ai_advisor_asset_swaps.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        no_api_key=no_api_key,
        symphonies=symphonies,
    )


@app.route("/ai-advisor/asset-swaps/evaluate", methods=["POST"])
def ai_advisor_asset_swaps_evaluate():
    """Operator-initiated swap evaluation endpoint (AC-2.1).

    Accepts JSON: { symphony_id, from_ticker, to_ticker, objective_type? }.
    Constructs a typed SwapObjective, fetches the baseline tree via symphony_logic,
    calls propose_operator_swap from advisors.asset_swap_engine, and returns the
    SwapRunResult fields as JSON.

    Never runs a live trade; never calls Composer write endpoints (AC-X1).
    Persistence (advisor_observation) is handled inside propose_operator_swap (AC-X3).

    Returns JSON with the swap result for rendering in the UI.
    """
    # Lazy imports (AC-X2 — keep asset_swap_engine off the live execution path).
    from advisors.asset_swap_engine import (  # noqa: PLC0415
        propose_operator_swap,
        SwapObjective,
        _has_composer_key,
    )
    from symphony_logic import fetch_symphony_score  # noqa: PLC0415

    if not _has_composer_key():
        return jsonify({"error": "advisor unavailable: API key not configured"}), 200

    body = request.get_json(silent=True) or {}
    symphony_id = str(body.get("symphony_id", "")).strip()
    from_ticker = str(body.get("from_ticker", "")).strip().upper()
    to_ticker = str(body.get("to_ticker", "")).strip().upper()
    # objective_type defaults to reduce_correlation for operator-initiated mode
    # (Gate-1 Resolution #2: every swap must be objective-directed; operator can
    # override via the optional form field).
    objective_type = str(body.get("objective_type", "reduce_correlation")).strip()

    if not symphony_id or not from_ticker or not to_ticker:
        return jsonify({"error": "symphony_id, from_ticker, and to_ticker are required"}), 200

    # AC-8: the payload carries the display NAME (from the analytics dropdown); the
    # Composer API needs the HASH.  Resolve NAME -> Composer hash via bot_state
    # (inverse of the AC-4 modal, which resolved hash -> name).
    # bot_state keys are Composer hashes; sym_data["name"] is the display name.
    composer_hash = symphony_id  # fallback: pass as-is if not found in bot_state
    _bot_state = database.load_state()
    for _sym_key, _sym_data in _bot_state.items():
        if not isinstance(_sym_data, dict) or "name" not in _sym_data:
            continue
        if database.normalize_name(_sym_data["name"]) == database.normalize_name(symphony_id):
            composer_hash = _sym_key
            break

    raw_value = fetch_symphony_score(composer_hash)
    if not raw_value:
        return jsonify({"error": f"could not fetch symphony tree for {symphony_id}"}), 200

    # Construct a typed SwapObjective (Gate-1 Resolution #2 — no plain string objectives).
    objective = SwapObjective(
        objective_type=objective_type,
        target_pair=None,
        measured_value=0.0,
    )

    try:
        run_result = propose_operator_swap(
            # Pass the Composer hash — engine uses it as the UUID for dvm_capital
            # unpacking in run_backtest (composer_backtest_client.py:269).
            symphony_id=composer_hash,
            score_tree=raw_value,
            incumbent_asset=from_ticker,
            candidate_asset=to_ticker,
            objective=objective,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_asset_swaps_evaluate failed: %s", exc, exc_info=True)
        return jsonify({"error": f"evaluation error: {exc}"}), 200

    # Build response from the first proposal (single-candidate operator-initiated mode)
    # plus the run-level message and gate batch metadata (AC-2.3 / AC-2.5).
    proposal = run_result.proposals[0] if run_result.proposals else None
    gate_result = proposal.gate_result if proposal else None

    return jsonify({
        # Run-level fields (AC-2.5: always expose the message so zero-survivors is explicit)
        "message": run_result.message,
        "survivors": len(run_result.survivors),
        "no_api_key": run_result.no_api_key,
        # Proposal-level fields (AC-2.3: stats + verdict + rationale + guidance)
        "candidate_id": proposal.candidate_id if proposal else None,
        "symphony_id": symphony_id,
        "from_ticker": from_ticker,
        "to_ticker": to_ticker,
        "objective_rationale": proposal.objective_rationale if proposal else "",
        "baseline_stats": proposal.baseline_stats if proposal else None,
        "variant_stats": proposal.variant_stats if proposal else None,
        # Gate verdict — AC-2.3: operator sees decision + reason
        "gate_decision": gate_result.verdict.decision if gate_result else None,
        "gate_result": {
            "decision": gate_result.verdict.decision,
            "validation_days": gate_result.validation_days,
            "oos_alpha": gate_result.oos_alpha,
            "winner_p_adj": gate_result.winner_p_adj,
        } if gate_result else None,
        # Caveats (mandatory for survivors — SURVIVOR_OVERFITTING_CAVEAT)
        "caveats": proposal.caveats if proposal else [],
        # Apply guidance — plain text, no button (AC-X1)
        "apply_guidance": proposal.apply_guidance if proposal else "",
        "backtest_error": proposal.backtest_error if proposal else None,
        "data_warnings": proposal.data_warnings if proposal else [],
    }), 200


@app.route("/ai-advisor/logic-changes", methods=["GET"])
def ai_advisor_logic_changes():
    """Render the M4 Logic Changes tab (AC-3.1..3.4, AC-X1..X5).

    Read-only surface — no writes to live positions, no Composer write endpoints.
    All logic-change proposals are advisory-only (apply manually in Composer).

    Template context:
      no_api_key  — True when Composer credentials are absent (AC-X4)
      symphonies  — list of known symphony IDs for the operator-initiated form
    """
    # Lazy import keeps the module off the live 1-minute execution path (AC-X2).
    from advisors.logic_change_engine import _has_composer_key  # noqa: PLC0415

    no_api_key = not _has_composer_key()

    # Provide the symphony list for the operator-initiated form.
    symphonies: list[str] = []
    try:
        import analytics as _analytics  # noqa: PLC0415
        history = _analytics.get_history_with_cache_invalidation(
            base_dir=_analytics._POST_MORTEMS_DIR
        )
        symphonies = _analytics.list_available_symphonies(history)
    except Exception:
        pass  # Symphony list is optional; the form still renders without it.

    return render_template(
        "ai_advisor_logic_changes.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        no_api_key=no_api_key,
        symphonies=symphonies,
    )


@app.route("/ai-advisor/logic-changes/evaluate", methods=["POST"])
def ai_advisor_logic_changes_evaluate():
    """Operator-initiated logic-change evaluation endpoint (AC-3.1).

    Accepts JSON: { symphony_id, objective_type?, change_description }.
    Parses the change_description to build a LogicTweak + LogicChangeObjective,
    fetches the baseline tree via symphony_logic, calls propose_operator_logic_change
    from advisors.logic_change_engine, and returns the LogicChangeRunResult fields
    as JSON.

    Never runs a live trade; never calls Composer write endpoints (AC-X1).
    Persistence (advisor_observation) is handled inside propose_operator_logic_change
    (AC-X3).

    The change_description is a plain-text operator input (e.g., "change momentum
    lookback from 20 to 10 days").  The route parses it for node_path + param_key +
    old_value + new_value via a simple heuristic; on parse failure it returns a clear
    error rather than fabricating a tweak.

    Returns JSON with the logic-change result for rendering in the UI.
    """
    # Lazy imports (AC-X2 — keep logic_change_engine off the live execution path).
    from advisors.logic_change_engine import (  # noqa: PLC0415
        propose_operator_logic_change,
        LogicTweak,
        LogicChangeObjective,
        _has_composer_key,
        NO_SURVIVORS_MESSAGE,
    )
    from symphony_logic import fetch_symphony_score  # noqa: PLC0415

    if not _has_composer_key():
        return jsonify({"error": "advisor unavailable: API key not configured"}), 200

    body = request.get_json(silent=True) or {}
    symphony_id = str(body.get("symphony_id", "")).strip()
    objective_type = str(body.get("objective_type", "reduce_drawdown")).strip()
    change_description = str(body.get("change_description", "")).strip()

    if not symphony_id or not change_description:
        return jsonify({"error": "symphony_id and change_description are required"}), 200

    # AC-8: same NAME->Composer-hash resolution as asset-swaps/evaluate (identical bug).
    # The payload carries the display NAME; the Composer API needs the HASH.
    composer_hash = symphony_id  # fallback: pass as-is if not found in bot_state
    _bot_state = database.load_state()
    for _sym_key, _sym_data in _bot_state.items():
        if not isinstance(_sym_data, dict) or "name" not in _sym_data:
            continue
        if database.normalize_name(_sym_data["name"]) == database.normalize_name(symphony_id):
            composer_hash = _sym_key
            break

    raw_value = fetch_symphony_score(composer_hash)
    if not raw_value:
        return jsonify({"error": f"could not fetch symphony tree for {symphony_id}"}), 200

    # Build a typed LogicChangeObjective (Gate-1 Resolution #2 — no plain-string objectives).
    objective = LogicChangeObjective(
        objective_type=objective_type,
        measured_value=0.0,
        rationale=change_description,
    )

    # Delegate parse + apply to the engine; pass change_description= so the engine's
    # own _parse_change_description_to_tweak runs internally.  On parse failure the
    # engine sets backtest_error on the proposal and returns zero survivors — no
    # early-return needed here (AC-X5 isolation applies at the engine level).
    try:
        run_result = propose_operator_logic_change(
            # Pass the Composer hash — engine uses it as the UUID for dvm_capital
            # unpacking in run_backtest (composer_backtest_client.py:269).
            symphony_id=composer_hash,
            score_tree=raw_value,
            tweak=None,
            objective=objective,
            change_description=change_description,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_logic_changes_evaluate failed: %s", exc, exc_info=True)
        return jsonify({"error": f"evaluation error: {exc}"}), 200

    # Build FDR metadata for the operator audit trail (AC-3.2).
    gate_batch = run_result.gate_batch
    proposal = run_result.proposals[0] if run_result.proposals else None
    gate_result = proposal.gate_result if proposal else None

    # Adjusted p-value threshold = fdr_q / c(n), where c(n) is the Yekutieli
    # harmonic-sum correction factor.  Derive from gate_batch fields; fall back to
    # gate_batch.fdr_q when c(n) is not directly available.
    fdr_adjusted_threshold: float | None = None
    if gate_batch is not None:
        import math as _math  # noqa: PLC0415
        n = gate_batch.n_candidates or 1
        # Yekutieli c(n) = sum(1/k for k in 1..n) — same formula as autotuner._c_yekutieli.
        c_n = sum(1.0 / k for k in range(1, n + 1))
        fdr_adjusted_threshold = gate_batch.fdr_q / c_n if c_n > 0 else gate_batch.fdr_q

    def _proposal_to_dict(p) -> dict:
        """Serialise a LogicChangeProposalResult to a JSON-friendly dict."""
        gr = p.gate_result
        return {
            "candidate_id": p.candidate_id,
            "symphony_id": p.symphony_id,
            "objective_type": p.objective.objective_type if p.objective else None,
            "objective_rationale": p.objective_rationale,
            "tweak_param_key": p.tweak.param_key if p.tweak else None,
            "tweak_old_value": p.tweak.old_value if p.tweak else None,
            "tweak_new_value": p.tweak.new_value if p.tweak else None,
            "tweak_node_path": p.tweak.node_path if p.tweak else None,
            "tweak_node_description": p.tweak.node_description if p.tweak else None,
            "baseline_stats": p.baseline_stats,
            "variant_stats": p.variant_stats,
            # Gate verdict (AC-3.3)
            "gate_decision": gr.verdict.decision if gr else None,
            "gate_reason": gr.verdict.reason if gr else None,
            "validation_days": gr.validation_days if gr else None,
            "oos_alpha": gr.oos_alpha if gr else None,
            "winner_p_adj": gr.winner_p_adj if gr else None,
            # FDR metadata for audit trail (AC-3.2)
            "n_candidates": gate_batch.n_candidates if gate_batch else None,
            "fdr_q": gate_batch.fdr_q if gate_batch else None,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            # Caveats (mandatory for survivors, AC-3.3)
            "caveats": p.caveats,
            # Apply guidance — plain text, no button (AC-X1 / AC-3.4)
            "apply_guidance": p.apply_guidance,
            "backtest_error": p.backtest_error,
            "data_warnings": p.data_warnings,
        }

    return jsonify({
        # Run-level fields (AC-3.1: zero survivors is valid, not silent)
        "message": run_result.message,
        "survivors": len(run_result.survivors),
        "no_api_key": run_result.no_api_key,
        # Proposal detail for rendering
        "survivors_detail": [_proposal_to_dict(p) for p in run_result.survivors],
        "rejected_detail": [_proposal_to_dict(p) for p in run_result.rejected_candidates],
        # Gate verdict shortcut (for tests that check flat gate_decision key)
        "gate_decision": gate_result.verdict.decision if gate_result else None,
        "gate_result": {
            "decision": gate_result.verdict.decision,
            "validation_days": gate_result.validation_days,
            "oos_alpha": gate_result.oos_alpha,
            "winner_p_adj": gate_result.winner_p_adj,
        } if gate_result else None,
        # FDR metadata at run level (AC-3.2)
        "n_candidates": gate_batch.n_candidates if gate_batch else None,
        "fdr_q": gate_batch.fdr_q if gate_batch else None,
        "fdr_adjusted_threshold": fdr_adjusted_threshold,
        # Caveats + guidance from the primary proposal (operator-initiated = single candidate)
        "caveats": proposal.caveats if proposal else [],
        "apply_guidance": proposal.apply_guidance if proposal else "",
        "backtest_error": proposal.backtest_error if proposal else None,
        "objective_rationale": proposal.objective_rationale if proposal else "",
    }), 200


def _compute_suggestion_gates(suggestion, symphony_id: str) -> dict:
    """Compute four_gates_verdict booleans for one suggestion (FP-T1-05).

    allowlist: key is in the suggestible allowlist.
    risk_direction: Claude's self-reported direction agrees with engine's.
    oos_frozen_eval: suggestion's oos_status is 'passed'.
    locked_vars: key is NOT locked for this symphony.
    """
    allowed, _ = ai_advisor.enforce_suggestion_allowlist([suggestion])
    direction_check = ai_advisor.check_risk_direction_agreement(suggestion)
    _, locked = ai_advisor._read_current_strategy(symphony_id)
    return {
        "allowlist": bool(allowed),
        "risk_direction": direction_check.get("agrees", False),
        "oos_frozen_eval": suggestion.oos_status == "passed",
        "locked_vars": suggestion.config_key not in locked,
    }


def _enrich_suggestion_impact(suggestion) -> dict:
    """Build impact dict with before/after/delta/metric fields.

    Claude emits impact as {"metric": ..., "delta": ...}. The frontend and tests
    expect all four keys: before, after, delta, and metric.
    delta = after - before (C-13: must be present in the response).
    """
    raw = suggestion.impact or {}
    metric = raw.get("metric", "sharpe")
    delta = float(raw.get("delta", 0.0))
    before = float(raw.get("before", 0.0))
    after = float(raw.get("after", before + delta))
    return {"before": before, "after": after, "delta": after - before, "metric": metric}


_DEV_ADVISOR_FIXTURE = [
    {
        "config_key": "TRIGGER_THRESHOLD_PCT",
        "current_value": 0.65,
        "suggested_value": 0.72,
        "rationale": (
            "Trailing 20-day realised vol has compressed to 0.83% (125d median 1.14%). "
            "Raising the MC-probability ceiling arms the guard later in the vol cycle, "
            "avoiding false triggers during low-dispersion regimes. Walk-forward Sharpe "
            "improves +0.18 across 3 of 4 out-of-sample windows."
        ),
        "risk_direction": "tightens",
        "confidence": "high",
        "data_sufficiency": "sufficient",
        "oos_status": "passed",
        "oos_reason": None,
        "impact": {"before": 1.42, "after": 1.60, "metric": "sharpe"},
        "four_gates_verdict": {
            "allowlist": True,
            "risk_direction": True,
            "oos_frozen_eval": True,
            "locked_vars": True,
        },
    },
    {
        "config_key": "VWAP_BLEED_MULTIPLIER",
        "current_value": 1.8,
        "suggested_value": 2.1,
        "rationale": (
            "VWAP defence fired 11 times in the past 60 sessions; 7 of those reversed "
            "within 12 minutes. Widening the bleed multiplier reduces premature bleed "
            "exits during high-frequency noise. DSR improves from 0.91 to 1.07 in the "
            "most recent 63-day out-of-sample window."
        ),
        "risk_direction": "loosens",
        "confidence": "medium",
        "data_sufficiency": "sufficient",
        "oos_status": "passed",
        "oos_reason": None,
        "impact": {"before": 0.91, "after": 1.07, "metric": "dsr"},
        "four_gates_verdict": {
            "allowlist": True,
            "risk_direction": True,
            "oos_frozen_eval": True,
            "locked_vars": True,
        },
    },
    {
        "config_key": "MAX_SQUEEZE_FLOOR",
        "current_value": 0.05,
        "suggested_value": 0.04,
        "rationale": (
            "Log-time squeeze is flooring too early: 23 of 31 recent squeezes hit the "
            "floor before the momentum signal resolved. Lowering the floor by 1pp "
            "allows the ratchet to tighten further on genuine momentum without "
            "changing the armed-guard trigger path."
        ),
        "risk_direction": "tightens",
        "confidence": "low",
        "data_sufficiency": "marginal",
        "oos_status": "rejected",
        "oos_reason": "OOS Sharpe degraded -0.11 in 2 of 4 windows; insufficient walk-forward support.",
        "impact": {"before": 1.42, "after": 1.31, "metric": "sharpe"},
        "four_gates_verdict": {
            "allowlist": True,
            "risk_direction": True,
            "oos_frozen_eval": False,
            "locked_vars": True,
        },
    },
]


@app.route("/ai-advisor/suggest", methods=["POST"])
def ai_advisor_suggest():
    """Call Claude advisor and return suggestions as JSON."""
    if os.environ.get("DEV_ADVISOR_FIXTURE"):
        return jsonify({"suggestions": _DEV_ADVISOR_FIXTURE})
    try:
        payload = request.json or {}
        symphony_id = payload.get("symphony_id", "")
        context = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=symphony_id)
        suggestions_response, error_msg = ai_advisor.request_suggestions(context)
        if error_msg is not None:
            return jsonify({"error": error_msg}), 200
        suggestions = []
        for s in suggestions_response.suggestions:
            s_dict = s.model_dump()
            s_dict["four_gates_verdict"] = _compute_suggestion_gates(s, symphony_id)
            s_dict["impact"] = _enrich_suggestion_impact(s)
            suggestions.append(s_dict)
        return jsonify({"suggestions": suggestions})
    except Exception as _exc:
        _daemon_log.error("ai_advisor_suggest failed: %s", _exc, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 200


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
    current_strategy_row = database.get_symphony_strategy(symphony_id) or {
        "params": {},
        "locked_vars": [],
    }
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

    # AC-5: persist the operator decision to the immutable llm_suggestions audit
    # trail so the table is no longer empty after an accepted suggestion.
    # before_value: the param's value before the config write; after_value: what
    # the operator accepted.  symphony_name is the canonical normalized form.
    _now = datetime.now(ZoneInfo("UTC")).isoformat()
    database.record_llm_suggestion(
        session_id=os.urandom(8).hex(),
        created_at=_now,
        symphony_name=database.normalize_name(symphony_id),
        operator_identity="",
        prompt_inputs={},
        model_id=ai_advisor._CLAUDE_MODEL,
        generation_settings={},
        raw_response={},
        validation_results={},
        param_name=suggestion_obj.config_key,
        operator_decision="accepted",
        decision_at=_now,
        before_value=flat_params.get(suggestion_obj.config_key),
        after_value=suggestion_obj.suggested_value,
        oos_revalidation=oos_result,
    )
    return jsonify({"status": "accepted"})


@app.route("/ai-advisor/reject", methods=["POST"])
def ai_advisor_reject():
    """Record operator rejection — no config write.

    AC-5: reads the payload so the rejection is persisted to the immutable
    llm_suggestions audit trail.  Never calls save_symphony_strategy.
    """
    payload = request.json or {}
    symphony_id = payload.get("symphony_id", "")
    suggestion_data = payload.get("suggestion", {})
    config_key = suggestion_data.get("config_key", "")

    _now = datetime.now(ZoneInfo("UTC")).isoformat()
    database.record_llm_suggestion(
        session_id=os.urandom(8).hex(),
        created_at=_now,
        symphony_name=database.normalize_name(symphony_id),
        operator_identity="",
        prompt_inputs={},
        model_id=ai_advisor._CLAUDE_MODEL,
        generation_settings={},
        raw_response={},
        validation_results={},
        param_name=config_key,
        operator_decision="rejected",
        decision_at=_now,
        before_value=suggestion_data.get("current_value"),
        after_value=suggestion_data.get("suggested_value"),
    )
    return jsonify({"status": "rejected"})


@app.route("/ai-advisor/chat", methods=["GET"])
def ai_advisor_chat():
    """Render the M5 Chat (explain-only) tab (AC-4.1..4.3, AC-X1..X3).

    Read-only surface — no writes to live positions, no Composer write endpoints,
    no DB writes of any kind.  Chat explains existing advisor artifacts; it does
    not generate new recommendations or accept/apply any changes.

    Template context:
      chat_available  — True when ANTHROPIC_API_KEY is present; the template
                        renders the 'chat unavailable' state (data-testid=
                        'chat-unavailable') when False (AC-4.3).
                        NEVER pass the API key value itself to the template.
      symphonies      — list of known symphony IDs for artifact-anchor display.
    """
    # chat_available: True only when the key is set.  We check existence only;
    # the value is NEVER passed to the template (AC-4.1 / security hygiene).
    chat_available = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # Provide the symphony list for artifact-context display.
    symphonies: list[str] = []
    try:
        import analytics as _analytics  # noqa: PLC0415
        history = _analytics.get_history_with_cache_invalidation(
            base_dir=_analytics._POST_MORTEMS_DIR
        )
        symphonies = _analytics.list_available_symphonies(history)
    except Exception:
        pass  # Symphony list is optional; the page still renders without it.

    return render_template(
        "ai_advisor_chat.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        chat_available=chat_available,
        symphonies=symphonies,
    )


@app.route("/ai-advisor/chat/send", methods=["POST"])
def ai_advisor_chat_send():
    """Explain-only chat send endpoint (AC-4.1..4.3, AC-X1..X3).

    Accepts JSON: { artifact_type, artifact_id, artifact, history, message }.
    Delegates to advisors.advisor_chat.explain_artifact; returns the LLM
    explanation as JSON.

    HARD constraints (AC-4.1):
      - MUST NOT call insert_advisor_observation, save_state, or any mutation.
      - MUST NOT call the OOS re-validation gate, suggest_swaps, or run_backtest.
      - MUST NOT reference Composer write endpoints.
      - Returns {reply: str} on success, {error: str} on any failure.
      - Never returns a 500 or an HTML error page (AC-4.3).

    Missing required fields (message, artifact) → 400 JSON error.
    LLM unavailable / error → 200 JSON {error: str} from explain_artifact.
    """
    # Lazy import keeps advisor_chat off the live 1-minute execution path (AC-X2).
    from advisors.advisor_chat import explain_artifact, validate_artifact  # noqa: PLC0415

    # Parse the JSON body; malformed bodies get a 400.
    body = request.get_json(silent=True) or {}

    message = body.get("message")
    artifact = body.get("artifact")

    if not message:
        return jsonify({"error": "missing required field: message"}), 400
    if artifact is None:
        return jsonify({"error": "missing required field: artifact"}), 400

    # --- AC-2b: message length cap ---
    # Reject oversized messages before they reach the paid LLM call.
    if len(message) > CHAT_MAX_MESSAGE_CHARS:
        return jsonify({"error": f"message exceeds maximum length of {CHAT_MAX_MESSAGE_CHARS} characters"}), 400

    # --- AC-2c: artifact size cap ---
    # Measure the artifact's JSON footprint before passing it to the LLM.
    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality
    artifact_json_size = len(_json.dumps(artifact))
    if artifact_json_size > CHAT_MAX_ARTIFACT_BYTES:
        return jsonify({"error": f"artifact exceeds maximum size of {CHAT_MAX_ARTIFACT_BYTES} bytes"}), 400

    # --- AC-2d: per-client sliding-window rate limit ---
    # Track requests per remote IP using a deque of timestamps.  No external
    # dependency required — collections.deque + time.time() is sufficient.
    _now = time.time()
    _client_ip = request.remote_addr or "unknown"
    if _client_ip not in _CHAT_RATE_LIMITER:
        # B-2: cap the total number of tracked IPs to bound memory growth.
        # When the dict is full, reject new IPs with 429 rather than growing
        # the dict further.  Existing tracked IPs are always admitted (their
        # key is already present) so legitimate operators are unaffected.
        if len(_CHAT_RATE_LIMITER) >= CHAT_RATE_LIMITER_MAX_TRACKED_IPS:
            return jsonify({"error": "rate limit exceeded — too many requests"}), 429
        _CHAT_RATE_LIMITER[_client_ip] = _collections.deque()
    _ip_window = _CHAT_RATE_LIMITER[_client_ip]
    # Expire timestamps outside the rolling window.
    while _ip_window and _now - _ip_window[0] > CHAT_RATE_LIMIT_WINDOW_SECONDS:
        _ip_window.popleft()
    # Evict the key when the window has fully expired — prevents unbounded dict
    # growth over a long daemon lifetime (IPs that stop requesting stay forever
    # otherwise, since the deque drains to empty but the key remains).
    if not _ip_window:
        del _CHAT_RATE_LIMITER[_client_ip]
        _CHAT_RATE_LIMITER[_client_ip] = _collections.deque()
        _ip_window = _CHAT_RATE_LIMITER[_client_ip]
    if len(_ip_window) >= CHAT_RATE_LIMIT_MAX_REQUESTS:
        return jsonify({"error": "rate limit exceeded — too many requests"}), 429
    _ip_window.append(_now)

    # --- AC-3: server-side artifact scoping ---
    # Strip unknown fields and bound string values before the artifact reaches
    # the Anthropic prompt.  validate_artifact is a pure function (no I/O).
    scoped_artifact = validate_artifact(artifact)

    # Delegate entirely to the chat backend — the explain-only boundary is
    # enforced inside explain_artifact and its system prompt.
    # question keyword matches explain_artifact(question, artifact) signature.
    result = explain_artifact(question=message, artifact=scoped_artifact)

    if result.answer is not None:
        return jsonify({"reply": result.answer})

    # result.error is non-None — degrade gracefully (AC-4.3).
    return jsonify({"error": result.error or "chat unavailable"})


# Known advisor roles — used to aggregate observations for the AI Advisor page.
_ADVISOR_ROLES = [
    "OVERFITTING_CONSCIENCE",
    "SPEC_CRITIC",
    "DIVERGENCE_EXPLAINER",
    "NARRATOR",  # DEFERRED per Sprint 3 scope — producer not yet shipped
]

# Hard limit on observations returned per request — prevents unbounded UI renders.
_ADVISOR_OBSERVATIONS_PAGE_LIMIT = 50


@app.route("/api/advisor-observations", methods=["GET"])
def api_advisor_observations():
    """Return advisor observations as a JSON list.

    ?symphony_id=<id>  — filter to rows whose denormalized symphony_id column
                         matches; calls database.get_advisor_observations_for_symphony
                         (single SELECT post-migration-025, S3-AUDIT-004/010).
    No query param      — return observations across all known advisor roles;
                         calls database.get_advisor_observations_for_role.

    Response is limited to _ADVISOR_OBSERVATIONS_PAGE_LIMIT rows.
    Read-only; POST/PUT/DELETE return 405 via Flask methods restriction.
    """
    symphony_id = request.args.get("symphony_id", "").strip()
    if symphony_id:
        # S3-AUDIT-004 + S3-AUDIT-010: single-query via the denormalized symphony_id
        # column (migration 025).  The legacy 3x subject_type fan-out used
        # subject_id==symphony_id which never matched: producers store subject_id
        # as a numeric PK (OC/DE) or bundle_hash (SC), not the symphony name.
        rows = database.get_advisor_observations_for_symphony(symphony_id)
    else:
        rows = []
        for role in _ADVISOR_ROLES:
            rows.extend(
                database.get_advisor_observations_for_role(
                    role, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
                )
            )

    rows = rows[: _ADVISOR_OBSERVATIONS_PAGE_LIMIT]
    return jsonify(rows)


_BASELINE_DECISION_TOKENS = {
    "Reverted to Fallback": "fallback",
    "Applied": "apply",
    "Rejected": "reject",
}

_FROZEN_EVAL_SHARPE_THRESHOLD = 0.0


def _normalize_autotune_row(row: dict) -> dict:
    """Normalize an autotune run dict for the /api/autotune-runs response (FP-T1-06).

    Converts verbose baseline_decision strings to short enum tokens and adds a
    frozen_eval_verdict derived field.
    """
    row = dict(row)
    raw_decision = row.get("baseline_decision")
    if raw_decision is not None:
        row["baseline_decision"] = _BASELINE_DECISION_TOKENS.get(raw_decision, raw_decision)
    frozen_sharpe = row.get("frozen_eval_sharpe")
    if frozen_sharpe is not None:
        row["frozen_eval_verdict"] = "passed" if frozen_sharpe >= _FROZEN_EVAL_SHARPE_THRESHOLD else "failed"
    else:
        row["frozen_eval_verdict"] = None
    return row


@app.route("/api/autotune-runs", methods=["GET"])
def api_autotune_runs():
    """Return recent autotune run rows including all three Sharpe metrics."""
    _ro_conn = database.get_ro_connection()
    try:
        rows = database.get_all_autotune_runs(limit=50)
        return jsonify([_normalize_autotune_row(r) for r in rows])
    finally:
        _ro_conn.close()


if __name__ == "__main__":
    # Reconfigure stdout to UTF-8 so emoji/non-Latin-1 chars don't crash on
    # Windows (cp1252 default).  Guarded to __main__ so pytest's capture is
    # not affected when this module is imported during test collection.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # Enforce daemon singleton BEFORE starting Flask or the scheduler thread.
    # If another Planet Stopper is alive this call prints an error and exits non-zero.
    # If a stale pidfile exists (ungraceful prior kill) it is overwritten cleanly.
    _acquire_daemon_singleton(_PIDFILE_PATH)

    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"\nStarting Alpha Bot Control Center at http://localhost:{port}\n")

    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=port, debug=False, use_reloader=False)
