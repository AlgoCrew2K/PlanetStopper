"""Flask application for Planet Stopper Control Center with Account-Level settings."""

import atexit
import concurrent.futures
import hmac
import io
import logging
import math
import os
import queue
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import dotenv as _dotenv_module
import psutil
import requests
import schedule
from dotenv import dotenv_values, load_dotenv, set_key
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import ai_advisor
import analytics
import database
import market_calendar
import model_config
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

# Load .env into os.environ for THIS process. The dashboard spawns
# alpha_bot_execution.py as a subprocess and never imports it, so its
# load_dotenv never runs here — os.getenv consumers in the dashboard
# (ai_advisor._build_client: chat + LLM suggestions) silently degraded to
# "no API key" on any daemon not launched from a shell exporting the keys.
# Found by live-daemon verification with real credentials, 2026-06-12.
load_dotenv(ENV_FILE_PATH)

app = Flask(__name__)
# Reload .html templates on every request without restarting the process.
# NOTE: use_reloader / debug auto-restart are intentionally NOT enabled — the
# process owns a minute-scheduler that spawns real-money execution subprocesses;
# a Python-code restart would interrupt live ops.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# ---------------------------------------------------------------------------
# Session cookie hardening (AC-10)
# ---------------------------------------------------------------------------
# These flags are set at startup; Flask honours them when writing the session
# cookie after each response.  SESSION_COOKIE_SECURE is env-driven so the
# operator can enable it when terminating TLS in a reverse proxy.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in (
    "1",
    "true",
    "yes",
)

# ---------------------------------------------------------------------------
# Dashboard auth gate — must be declared before CSRF so its @before_request
# hook registers first (auth runs before CSRF; CSRF guard is irrelevant when
# auth already denies the request).
# ---------------------------------------------------------------------------

# Module-level flag — test suite sets this to False via the _disable_auth_for_tests
# autouse fixture in tests/conftest.py so existing route tests hit protected routes
# without being redirected.  Mirrors the _csrf_check_enabled pattern.  Never set
# False in production code.
_auth_check_enabled: bool = True

# Minimal set of endpoints exempt from the auth gate.  'static' must be
# included so the login page renders its CSS/JS; 'login' and 'logout' are the
# auth-flow routes themselves; 'get_csrf_token' is safe (no sensitive data).
_AUTH_EXEMPT_ENDPOINTS: frozenset[str] = frozenset(
    {"login", "logout", "static", "get_csrf_token", "health"}
)

# In-memory throttle: maps client IP -> (fail_count, lockout_until_timestamp).
# Reset per-process; intentionally simple — single-operator dashboard.
_AUTH_FAILED_ATTEMPTS: dict[str, tuple[int, float]] = {}

# Maximum consecutive wrong-password attempts before a lockout is imposed.
_AUTH_MAX_ATTEMPTS: int = 10

# How long (seconds) a client is locked out after exceeding _AUTH_MAX_ATTEMPTS.
_AUTH_LOCKOUT_SECONDS: int = 300  # 5 minutes


def _resolve_dashboard_credential() -> str | None:
    """Return the dashboard credential from environment, preferring the hash.

    DASHBOARD_PASSWORD_HASH takes precedence over DASHBOARD_PASSWORD.  Returns
    None when neither is configured (misconfig → fail-closed).
    """
    hashed = os.environ.get("DASHBOARD_PASSWORD_HASH", "").strip()
    if hashed:
        return hashed
    plain = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    if plain:
        return plain
    return None


def _secret_key_configured() -> bool:
    """Return True when a non-empty secret key is present in the environment."""
    return bool(
        os.environ.get("SECRET_KEY", "").strip() or os.environ.get("FLASK_SECRET_KEY", "").strip()
    )


def _check_throttle(client_ip: str) -> bool:
    """Return True if the client is currently locked out."""
    entry = _AUTH_FAILED_ATTEMPTS.get(client_ip)
    if entry is None:
        return False
    fail_count, lockout_until = entry
    if fail_count < _AUTH_MAX_ATTEMPTS:
        # Not yet locked out; let the request through.
        return False
    # Locked out — check whether the lockout window has expired.
    if time.time() < lockout_until:
        return True
    # Lockout has expired — clear so a fresh attempt window starts.
    _AUTH_FAILED_ATTEMPTS.pop(client_ip, None)
    return False


def _record_failed_attempt(client_ip: str) -> int:
    """Increment the failed-attempt counter; return the new count."""
    entry = _AUTH_FAILED_ATTEMPTS.get(client_ip, (0, 0.0))
    new_count = entry[0] + 1
    lockout_until = time.time() + _AUTH_LOCKOUT_SECONDS if new_count >= _AUTH_MAX_ATTEMPTS else 0.0
    _AUTH_FAILED_ATTEMPTS[client_ip] = (new_count, lockout_until)
    return new_count


def _clear_failed_attempts(client_ip: str) -> None:
    """Reset the throttle counter for a client on successful login."""
    _AUTH_FAILED_ATTEMPTS.pop(client_ip, None)


def _is_api_or_xhr() -> bool:
    """Return True when the request looks like a JSON/XHR API call."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With", "") == "XMLHttpRequest"
    )


@app.before_request
def _auth_before_request():
    """Enforce dashboard password auth on every request (AC-1/AC-2/AC-8).

    Registered BEFORE _csrf_before_request so auth runs first; a denied
    request never reaches the CSRF gate.
    """
    # Bypass in test contexts — see _disable_auth_for_tests in tests/conftest.py.
    if not _auth_check_enabled:
        return None

    # Exempt login/logout/static so those routes remain reachable pre-auth.
    if request.endpoint in _AUTH_EXEMPT_ENDPOINTS:
        return None

    # Fail-closed: if the secret key is missing/empty, deny ALL requests.
    # Without a secret key Flask cannot sign the session cookie, and the
    # operator misconfigured the deployment.
    if not _secret_key_configured():
        _daemon_log.warning(
            "Auth misconfig: SECRET_KEY / FLASK_SECRET_KEY is not set"
            " — all requests denied until a secret key is configured"
        )
        if _is_api_or_xhr():
            return jsonify({"error": "misconfigured"}), 503
        return redirect(url_for("login"))

    # Fail-closed: if no credential is configured, deny ALL requests.
    if _resolve_dashboard_credential() is None:
        _daemon_log.warning(
            "Auth misconfig: DASHBOARD_PASSWORD / DASHBOARD_PASSWORD_HASH is not set"
            " — all requests denied until a credential is configured"
        )
        if _is_api_or_xhr():
            return jsonify({"error": "misconfigured"}), 503
        return redirect(url_for("login"))

    # Authenticated session — let the request through.
    if session.get("authenticated"):
        return None

    # Not authenticated — redirect HTML requests to login, 401 for API/XHR.
    if _is_api_or_xhr():
        return jsonify({"error": "unauthenticated"}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page: GET renders the form; POST processes the credential."""
    if request.method == "GET":
        # Already authenticated — send to dashboard (AC-13).
        if session.get("authenticated"):
            return redirect(url_for("dashboard"))
        return render_template("login.html", csrf_token=_CSRF_TOKEN, error=None)

    # POST — process the login attempt.  Do NOT short-circuit on an authenticated
    # session here: the credential must be re-verified so a wrong-password POST
    # is always denied, even if a prior request set the session.
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if os.environ.get("TRUST_PROXY") and request.headers.get("X-Forwarded-For")
        else request.remote_addr or "unknown"
    )

    # Fail-closed: misconfig → cannot authenticate.
    if not _secret_key_configured():
        return render_template(
            "login.html", csrf_token=_CSRF_TOKEN, error="Service misconfigured."
        ), 503
    credential = _resolve_dashboard_credential()
    if credential is None:
        return render_template(
            "login.html", csrf_token=_CSRF_TOKEN, error="Service misconfigured."
        ), 503

    # Throttle check.
    if _check_throttle(client_ip):
        return render_template(
            "login.html",
            csrf_token=_CSRF_TOKEN,
            error="Too many failed attempts. Please wait before trying again.",
        ), 429

    submitted = request.form.get("password", "")

    # Constant-time comparison to guard against timing attacks.
    # If the stored credential looks like a werkzeug hash, use its verifier;
    # otherwise compare as plaintext with hmac.compare_digest.
    is_hashed = credential.startswith(("pbkdf2:", "scrypt:", "bcrypt:"))
    if is_hashed:
        try:
            match = check_password_hash(credential, submitted)
        except Exception:
            match = False
    else:
        match = hmac.compare_digest(credential.encode(), submitted.encode())

    if not match:
        fail_count = _record_failed_attempt(client_ip)
        if fail_count >= _AUTH_MAX_ATTEMPTS:
            return render_template(
                "login.html",
                csrf_token=_CSRF_TOKEN,
                error="Too many failed attempts. Please wait before trying again.",
            ), 429
        return render_template(
            "login.html",
            csrf_token=_CSRF_TOKEN,
            error="Incorrect password.",
        ), 200

    # Successful login — clear any prior session data before setting auth flag
    # to prevent session-fixation attacks.
    _clear_failed_attempts(client_ip)
    session.clear()
    session["authenticated"] = True
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["GET"])
def logout():
    """Clear the session and redirect to the login page."""
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# CSRF protection (A-3)
# ---------------------------------------------------------------------------
# Per-daemon-startup secret key.  Each process restart rotates the token,
# which is fine — the operator refreshes the dashboard naturally.
# TESTING mode skips enforcement so test_client() POST calls work without
# injecting a token header (tests use monkeypatch on _csrf_check_enabled).
app.secret_key = (
    os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
)

# Module-level flag — test suite sets this to False via monkeypatch to bypass
# CSRF checks on the test client.  Never set False in production code.
_csrf_check_enabled: bool = True

# One process-lifetime CSRF token.  Operator dashboards are single-user
# single-tab; a per-session token would require cookie round-trips that
# complicate the JS fetch() callers.  The attacker barrier is the token
# itself, not its rotation frequency.
_CSRF_TOKEN: str = secrets.token_hex(32)


def _validate_csrf() -> None:
    """Reject POST requests that lack the correct CSRF token.

    Two acceptance channels (both must be same-origin):
    - X-CSRF-Token request header — used by fetch()/XHR callers (JSON POSTs
      from the dashboard JS).  Browsers block cross-site scripts from setting
      arbitrary request headers, so the header itself acts as the synchronizer
      token for those callers.
    - csrf_token form field — used by the native browser form POST on the login
      page, which cannot set custom headers.  The form embeds the server-minted
      token in a hidden input; same-origin enforcement is provided by the
      synchronizer-token pattern (token is not guessable by a cross-site page).
      The form-field channel is ONLY activated for form-encoded content types
      (application/x-www-form-urlencoded, multipart/form-data).  Accessing
      request.form on a JSON POST triggers Werkzeug body parsing, which enforces
      MAX_CONTENT_LENGTH before this CSRF check can fire — breaking the
      CSRF-before-body-size guard ordering invariant.

    No new pip dependencies required.
    """
    if not _csrf_check_enabled:
        return
    # Header channel: fetch()/XHR callers set X-CSRF-Token.
    token = request.headers.get("X-CSRF-Token", "")
    # Form-field channel: native browser form POST (login page).  Gate on
    # content-type so we never touch request.form on JSON requests — that
    # would trigger body parsing and fire 413 before we can return 403.
    if not token:
        ct = request.content_type or ""
        if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
            token = request.form.get("csrf_token", "")
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


@app.route("/health")
def health():
    """Minimal unauthenticated liveness probe (F-005).

    Read-only: sources last_successful_cycle_at from database.load_state()
    (the same top-level engine-written field app.py:2327 already reads) —
    never opens a read-write connection. Exempt from the auth gate via
    _AUTH_EXEMPT_ENDPOINTS (endpoint name 'health'). GET-only; POST 405s
    via Flask's default routing (no methods=["POST"] registered).
    """
    try:
        _state = database.load_state()
    except Exception:
        _state = {}
    return jsonify(
        {
            "status": "ok",
            "daemon_started_at": _DAEMON_STARTED_AT,
            "last_successful_cycle_at": _state.get("last_successful_cycle_at"),
        }
    )


log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# Single-worker executor for dashboard background writes (fleet alert dismiss).
# A persistent worker thread owns the I/O so Flask request handlers return
# immediately without blocking on SQLite.  Single-worker is intentional:
# serialises writes so no two dismiss tasks race on fleet_alert_state.
_DISMISS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
# CC-003: register shutdown so in-flight dismiss writes are not abandoned on exit.
atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True)

# Dedicated single-worker executor for the Frontrunner Builder's on-demand
# /run trigger (feature-plans/frontrunner-builder.md AC-8/AC-1). Deliberately
# NOT the _DISMISS_EXECUTOR above: run_frontrunner_build iterates every live
# symphony (up to MAX_CASCADES_PER_SYMPHONY_RUN cascades each) with
# rate-limited Fable + Composer calls and is genuinely multi-minute — sharing
# a pool with the latency-sensitive dismiss/flush writes would queue those
# behind a long-running build. Single-worker serialises overlapping run
# requests rather than hammering Fable/Composer concurrently.
_FRONTRUNNER_BUILD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
atexit.register(_FRONTRUNNER_BUILD_EXECUTOR.shutdown, wait=True)

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
#
# Uses _StaleFlagDict: _notify_cycle_complete() sets the stale flag (O(1), no
# lock needed) so that .get()/.keys() return empty-state immediately after a
# cycle completes.  _refresh_account_totals() clears the flag when it writes
# fresh values under _account_totals_cache_lock, preventing a partial-write
# window that a bare .clear() would expose.


class _StaleFlagDict(dict):
    """dict subclass whose reads return empty-state when marked stale.

    - mark_stale(): called by _notify_cycle_complete() after a cycle; reads
      immediately return None / empty until refresh_written() is called.
    - refresh_written(): called by _refresh_account_totals() after a
      successful write; clears the stale flag so reads see fresh values.
    - Writes always succeed regardless of the stale flag so the scheduler
      can populate the cache while it is still marked stale.
    - .clear() resets the stale flag (mirrors normal dict.clear semantics).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stale: bool = False

    def mark_stale(self) -> None:
        """Mark cache stale; reads return None until refresh_written()."""
        self._stale = True

    def refresh_written(self) -> None:
        """Clear the stale flag after _refresh_account_totals writes new values."""
        self._stale = False

    # ------------------------------------------------------------------
    # dict read protocol: return None / raise KeyError when stale
    # ------------------------------------------------------------------

    def get(self, key, default=None):
        if self._stale:
            return default
        return super().get(key, default)

    def __getitem__(self, key):
        if self._stale:
            raise KeyError(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        if self._stale:
            return False
        return super().__contains__(key)

    def keys(self):
        if self._stale:
            return {}.keys()
        return super().keys()

    def values(self):
        if self._stale:
            return {}.values()
        return super().values()

    def items(self):
        if self._stale:
            return {}.items()
        return super().items()

    def clear(self):
        """Clear underlying data and reset the stale flag."""
        super().clear()
        self._stale = False


_account_totals_cache: _StaleFlagDict = _StaleFlagDict()
# Serializes multi-key writes from _refresh_account_totals so that a
# concurrent reader never observes a partial write sequence.
_account_totals_cache_lock = threading.Lock()

# Last-good snapshot: plain dict (NOT _StaleFlagDict) so it survives mark_stale() calls
# on _account_totals_cache.  Populated on every successful Composer fetch.  Used as
# Tier-1 stale-cache fallback on both the live and frozen /api/state paths so that a
# transient Composer timeout never silently flips to unlabelled VW values.
_account_totals_last_good: dict = {}
# ET-format timestamp written on each successful _refresh_account_totals call.
# Surfaced as portfolio_strip["account_basis_as_of"] when the Tier-1 fallback fires.
_account_totals_last_success_at: str | None = None
# Named constant for the Composer HTTP timeout; promotes the bare literal at line 769.
_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 30
# F-010: cumulative count of known Composer read-timeouts hit by
# _refresh_account_totals — surfaced as aggregation context in the compact
# one-line log below instead of a full traceback per occurrence. _refresh_
# account_totals has 3 real concurrent call sites (the minute-scheduler
# tick, a _notify_cycle_complete-spawned thread, and a flush-resync
# thread), so the increment is protected by _account_totals_cache_lock
# (this function's existing convention for its other shared-state writes)
# — never an unsynchronized read-modify-write.
_account_totals_timeout_count = 0
# ET-format timestamp string used for account_basis_as_of / _account_totals_last_success_at
# across _refresh_account_totals and both the live and frozen stale-cache fallback paths.
_ACCOUNT_BASIS_TS_FMT = "%Y-%m-%d %H:%M:%S ET"

# SSE client registry — one Queue per connected /api/events client.
# _notify_cycle_complete() fans out a sentinel to each queue under the lock.
_sse_clients: list = []
_sse_clients_lock = threading.Lock()

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
def _notify_cycle_complete() -> None:
    """Fan out a cycle-complete notification to all connected SSE clients.

    Called from trigger_alpha_bot() in a finally block — must never raise.
    trigger_alpha_bot() is always invoked via threaded_trigger() (a daemon
    thread spawned by the per-minute scheduler), so this function runs in
    that daemon thread, NOT the scheduler thread.

    Sequence:
      1. Mark _account_totals_cache stale so /api/state returns empty-state
         immediately rather than prior-cycle data.  mark_stale() is used instead
         of .clear() because all cache readers (_compute_portfolio_strip / get_state)
         are lock-free: a bare .clear() under the write lock would not prevent a
         reader from observing a partially-populated dict mid-write.  mark_stale()
         masks ALL reads atomically in O(1).
      2. Spawn a short-lived daemon thread calling _refresh_account_totals() so the
         cache unmasks with FRESH post-cycle Composer data.  The thread is started
         BEFORE the SSE fan-out so the Composer API call is in-flight while the
         put_nowait loop runs (O(1) per client).  By the time the connected client
         receives the event and its /api/state fetch arrives (~50-200 ms network
         round-trip + JS processing), the refresh thread has typically finished its
         single Composer call (~100-500 ms).  Without this, the cache stays masked
         until the NEXT per-minute _refresh_account_totals tick (~55 s) — the
         SSE-triggered fetch would hit the blank window and show "--" for totals,
         defeating the feature.  If Composer is unreachable the thread completes
         without calling refresh_written(); the cache stays masked and the AC-8
         staleness cue fires (honest degradation).
         _notify_cycle_complete itself stays well under the 100 ms non-blocking
         budget: thread spawn + queue puts are O(1) and do no I/O.
      3. Fan out the "cycle-complete" SSE event.
    """
    # Step 1: atomically mask stale data from all lock-free readers.
    _account_totals_cache.mark_stale()

    # Step 2: start the refresh before the fan-out so it has maximum lead time.
    # _refresh_account_totals never raises (D-1 contract); daemon=True so it
    # does not prevent process exit.
    threading.Thread(target=_refresh_account_totals, daemon=True, name="cycle-refresh").start()

    # Step 3: fan out SSE notification (O(1) per client, no I/O).
    with _sse_clients_lock:
        clients = list(_sse_clients)

    for q in clients:
        try:
            q.put_nowait("cycle-complete")
        except Exception:
            pass  # full or closed — skip, do not raise


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
    finally:
        _notify_cycle_complete()


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
            f"[retention] pruned {shadow_deleted} old shadow_history rows (>{shadow_retention_days}d)"  # noqa: E501  # un-wrappable long line
        )


def _refresh_account_totals() -> None:
    """Fetch Composer account-level total-stats and populate _account_totals_cache.

    Called by the minute scheduler — must never raise (swallows all exceptions).
    On non-200 or any exception the stale flag (set by _notify_cycle_complete)
    is NOT cleared; reads continue to return None until the next successful call.
    On success, writes all keys under _account_totals_cache_lock and then calls
    _account_totals_cache.refresh_written() to clear the stale flag atomically.
    Auth pattern mirrors alpha_bot_execution.get_composer_headers().
    """
    global _account_totals_last_good, _account_totals_last_success_at, _account_totals_timeout_count
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
        resp = requests.get(url, headers=headers, timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S)
        if resp.status_code == 200:
            data = resp.json()
            # Acquire the shared cache lock before writing multiple keys so that
            # concurrent readers never observe a partial-write sequence.
            # After all keys are written, clear the stale flag so reads see the
            # fresh values (the flag was set by _notify_cycle_complete at cycle end).
            with _account_totals_cache_lock:
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
                # All keys written; clear the stale flag so reads now return fresh values.
                _account_totals_cache.refresh_written()
                # Snapshot to last-good (plain dict — survives future mark_stale calls).
                # Atomic reassignment (not clear()+update()): a reader between those two
                # steps would see an empty dict — a TOCTOU window where a stale-cache read
                # observes neither the fresh cache nor the prior last-good. Rebinding to a
                # new dict is a single reference swap; readers always see a complete dict.
                # Written only on genuine success (inside the lock, after refresh_written)
                # so _account_totals_last_success_at reflects the last real Composer fetch.
                _account_totals_last_good = dict(_account_totals_cache)
            # Advance the as-of timestamp only after all writes completed without error.
            _account_totals_last_success_at = datetime.now(_ET).strftime(_ACCOUNT_BASIS_TS_FMT)
        else:
            _daemon_log.warning(
                "_refresh_account_totals: Composer returned %s — cache unchanged",
                resp.status_code,
            )
    except requests.exceptions.ReadTimeout:
        # F-010: the known Composer read-timeout case (~30/day in production)
        # gets a compact one-line WARNING with aggregation context instead of
        # a full traceback. Any OTHER requests exception (ConnectionError,
        # etc.) or unexpected exception type is NOT caught here — it falls
        # through to the except Exception branch below and keeps its full
        # traceback (timeout-only match). The increment is a real
        # read-modify-write shared across 3 concurrent call sites (scheduler
        # tick, cycle-complete thread, flush-resync thread), so it's
        # protected by this function's existing shared-state lock; the log
        # call reads a stable post-lock snapshot rather than holding the
        # lock during logging.
        with _account_totals_cache_lock:
            _account_totals_timeout_count += 1
            _timeout_count_snapshot = _account_totals_timeout_count
        _daemon_log.warning(
            "_refresh_account_totals: Composer read-timeout (#%d, timeout=%ss) — cache unchanged",
            _timeout_count_snapshot,
            _ACCOUNT_TOTALS_HTTP_TIMEOUT_S,
        )
    except Exception as _exc:
        _daemon_log.error(
            "_refresh_account_totals failed — account totals cache unchanged: %s",
            _exc,
            exc_info=True,
        )


def _lens_pipeline_worker() -> None:
    """Background worker that runs the off-hours lens pipeline.

    Imported lazily to keep advisors.lens_pipeline off the execution path (CC-2).
    D-1 error contract: only type(exc).__name__ appears in log records at WARNING+.
    """
    try:
        from advisors.lens_pipeline import run_pipeline  # lazy — not module-level (CC-2)

        result = run_pipeline()
        _daemon_log.info("Lens pipeline complete: %s", result)
    except Exception as exc:
        _daemon_log.error("Lens pipeline worker failed: %s", type(exc).__name__)


def _run_lens_pipeline() -> None:
    """Non-blocking daily off-hours wrapper for the lens pipeline (AC-7).

    Spawns a daemon thread so the scheduler thread returns immediately —
    the pipeline never blocks the 1-minute execution path (arch constraint 1).
    """
    if os.environ.get("DISABLE_DAEMON_LENS_PIPELINE"):
        _daemon_log.info("Lens pipeline skipped (DISABLE_DAEMON_LENS_PIPELINE set).")
        return

    import threading

    t = threading.Thread(target=_lens_pipeline_worker, daemon=True, name="lens-pipeline")
    t.start()


def _incubation_tick_worker() -> None:
    """Background worker that runs the Strategy Incubation Gate's daily tick.

    Imported lazily to keep advisors.incubation off the execution path (CC-2).
    D-1 error contract: only type(exc).__name__ appears in log records at WARNING+.
    """
    try:
        from advisors.incubation import run_incubation_tick  # lazy — not module-level (CC-2)

        run_incubation_tick()
        _daemon_log.info("Incubation tick complete.")
    except Exception as exc:
        _daemon_log.error("Incubation tick worker failed: %s", type(exc).__name__)


def _run_incubation_tick() -> None:
    """Non-blocking daily off-hours wrapper for the incubation tick (AC-3).

    Spawns a daemon thread so the scheduler thread returns immediately —
    the tick never blocks the 1-minute execution path (arch constraint 1).
    """
    import threading

    t = threading.Thread(target=_incubation_tick_worker, daemon=True, name="incubation-tick")
    t.start()


# PR#140 /code-review finding 1 (Cycle 2c, AC-4): the number of decimal
# places every float in a retirement evidence snapshot is rounded to before
# comparison -- named so both _round_floats and its docstrings share one
# source of truth.
_MATERIAL_CHANGE_ROUND_NDIGITS = 2


def _round_floats(value, ndigits: int):
    """Recursively round every float in `value` to `ndigits` places.

    dict/list shapes are walked (into candidate_metrics/sibling_metrics);
    bool/int/str/None pass through unchanged. bool MUST be checked before
    float/int (bool is an int subclass in Python, and int is never rounded
    here -- see n_obs, which is intentionally compared exactly).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {k: _round_floats(v, ndigits) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_floats(v, ndigits) for v in value]
    return value


def _canonical_evidence_snapshot(rec: dict) -> dict:
    """Return a rounded snapshot of every evidence field in `rec` EXCEPT
    "explanation" (the value being reused) and the pair-identity keys
    "candidate_id"/"sibling_id" (already matched by construction to find
    this prior row -- not citable evidence).

    PR#140 /code-review finding 1 (Cycle 2c, AC-4): the whole-dict design
    replaces a curated field-list comparison -- advisors.retirement_
    explainer._build_explain_messages json.dumps()s the WHOLE rec and
    instructs the LLM to ground claims in ALL supplied evidence, so a
    curated subset could miss a materially-drifted field (e.g.
    stressed_correlation, ci_lower/ci_upper, n_obs, or an individual
    candidate_metrics/sibling_metrics value) the LLM could still cite. Every
    float is rounded recursively (including into nested metrics dicts); a
    key missing on either side of the comparison makes the two snapshots
    NOT equal (plain dict `==` is exact key-set + value equality) -- this
    subsumes the freshness-on-rename rule and the legacy-row rule for free,
    with zero special-casing. Future-proof against schema growth: a NEW
    raw_response field automatically joins the comparison.
    """
    excluded = {"explanation", "candidate_id", "sibling_id"}
    snapshot = {k: v for k, v in rec.items() if k not in excluded}
    return _round_floats(snapshot, _MATERIAL_CHANGE_ROUND_NDIGITS)


def _build_retirement_prior_row_map(rows: list[dict]) -> dict[tuple, dict]:
    """Build a {(candidate_id, sibling_id): newest_matching_raw_response}
    map from a newest-first advisor_observations row list.

    PR#140 /code-review finding 2 (Cycle 2c, AC-4 efficiency): hoists the
    reuse-lookup to ONE query per tick instead of a fresh role-wide DB query
    per rec -- _retirement_recommender_tick_worker builds this map once from
    a single database.get_advisor_observations_for_role() call, then every
    rec's reuse decision is an O(1) dict lookup against it. Only the FIRST
    (i.e. newest, since `rows` is newest-first by id DESC) row per pair is
    kept -- matches the pre-existing "most recent prior row governs"
    contract. Malformed rows (non-dict raw_response) are skipped, never
    raised.
    """
    prior_map: dict[tuple, dict] = {}
    for row in rows:
        raw = row.get("raw_response")
        if not isinstance(raw, dict):
            continue
        key = (raw.get("candidate_id"), raw.get("sibling_id"))
        if key not in prior_map:
            prior_map[key] = raw
    return prior_map


def _retirement_find_reusable_prior_explanation(rec: dict, prior_map: dict) -> str | None:
    """Return a prior explanation text reusable for `rec`, or None to force a
    fresh explain_recommendation call (Cycle 2c, AC-4: nightly-explain spend
    control).

    `prior_map` is a {(candidate_id, sibling_id): newest_matching_raw_response}
    map pre-built ONCE per tick via _build_retirement_prior_row_map (PR#140
    review finding 2) -- an O(1) lookup here, no DB access, no per-rec query.

    Reuse-eligible iff the prior row's explanation is truthy AND
    _canonical_evidence_snapshot(rec) == _canonical_evidence_snapshot(prior_raw)
    (PR#140 review finding 1 -- see that function's docstring for the full
    rationale). Fail-open: the whole lookup + comparison is ONE unit -- ANY
    exception anywhere inside it resolves to "no match", i.e. returns None,
    identically to the "no prior history" case. Never propagates.
    """
    try:
        key = (rec.get("candidate_id"), rec.get("sibling_id"))
        prior_raw = prior_map.get(key)
        if prior_raw is None:
            return None

        prior_explanation = prior_raw.get("explanation")
        if not prior_explanation:
            return None

        if _canonical_evidence_snapshot(rec) != _canonical_evidence_snapshot(prior_raw):
            return None

        return prior_explanation
    except Exception:
        return None


def _retirement_recommender_tick_worker() -> None:
    """Background worker that runs the Retirement Recommender's daily tick.

    Imported lazily to keep advisors.retirement_recommender off the execution
    path (CC-2). Calls the real producer chain -- build_recommendations() then
    persist_recommendations(<its own return value>) -- so the read-only
    GET /api/retirement-recommendations route and the AI Advisor panel have
    something to render (PM ruling, review-response cycle 2: without this
    tick nothing in production ever calls the producer, so both read-only
    surfaces render an honest-empty state forever).

    Cycle 2b (AC-2): each recommendation is run through advisors.
    retirement_explainer.explain_recommendation and the result is stamped
    onto rec["explanation"] -- this keeps the LLM entirely out of
    advisors/retirement_recommender.py (byte-unchanged this cycle, pinned by
    a golden hash test) and out of the render/approve/checklist paths
    (operator ruling, Gate-2b). A per-rec explainer failure degrades that
    one recommendation's explanation to None and never blocks persistence
    of the rest of the batch.

    Cycle 2c (AC-2): before its own explain-or-reuse decision, each rec is
    enriched in place with candidate_name/sibling_name resolved from live
    database.load_state() (honest raw-id fallback when unresolvable or on a
    load failure; only set when the key is absent, never clobbering a
    caller-supplied value) -- this both grounds the explainer's prompt in
    readable names and (critically) lands those two keys in the dict that
    flows into persist_recommendations, which AC-4's reuse check depends on
    to detect a rename against a prior night's persisted row. A bot_state
    load failure degrades the name fields to the raw id but never skips the
    explainer call itself.

    Cycle 2c (AC-4): before calling the explainer, each rec is checked
    against a prior-row map (built ONCE per tick, PR#140 review finding 2)
    via _retirement_find_reusable_prior_explanation for a reusable prior
    explanation (nightly-explain spend control, whole-dict canonical-
    snapshot equality per PR#140 review finding 1) -- when found, the prior
    text is reused verbatim and explain_recommendation is skipped for that
    rec; otherwise a fresh explanation is generated exactly as before. This
    decision is made independently per rec in the batch. Enrichment and the
    reuse-or-explain decision are one merged pass over recs (PR#140 review
    finding 5) -- enrichment for a given rec always completes before that
    SAME rec's reuse check runs, since both live in the same loop iteration.

    Cycle 2d (AC-2, PR#140 2nd /code-review findings 2+3): immediately after
    name enrichment and BEFORE the reuse-or-explain decision, each rec's
    numeric evidence is rounded to _MATERIAL_CHANGE_ROUND_NDIGITS via
    _round_floats and reassigned onto recs[i] (the function is pure -- a
    rebind of the loop-local name alone would not propagate). This makes
    the citation precision the explainer sees equal the comparison
    precision the reuse gate checks, and equal what ultimately gets
    persisted -- regardless of whether that rec's explanation is reused or
    freshly generated.

    D-1 error contract: only type(exc).__name__ appears in log records at
    WARNING+ -- a producer failure must never crash the scheduler thread.
    """
    try:
        from advisors.retirement_recommender import (  # lazy — not module-level (CC-2)
            build_recommendations,
            persist_recommendations,
        )

        recs = build_recommendations()
        if recs:
            from advisors.retirement_explainer import explain_recommendation  # lazy (CC-2)

            try:
                _ret_bot_state = database.load_state()
            except Exception:
                _ret_bot_state = {}
            try:
                from advisors.frontrunner_builder import (  # lazy (CC-2)
                    resolve_incumbent_display_name as _resolve_ret_name,
                )
            except Exception:
                _resolve_ret_name = None

            # PR#140 review finding 2: ONE role-wide query per tick (not one
            # per rec) -- builds the {(candidate_id, sibling_id): raw} map
            # every rec's reuse decision below looks up against.
            try:
                _prior_rows = database.get_advisor_observations_for_role(
                    "RETIREMENT_RECOMMENDATION", limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
                )
                _prior_map = _build_retirement_prior_row_map(_prior_rows)
            except Exception:
                _prior_map = {}

            for _i, rec in enumerate(recs):
                # A real build_recommendations() rec never carries these keys
                # (they are not part of the recommender's authoritative
                # schema) -- only set them when absent, never clobber a
                # value a caller already supplied.
                if "candidate_name" not in rec:
                    rec["candidate_name"] = (
                        _resolve_ret_name(_ret_bot_state, rec.get("candidate_id"))
                        if _resolve_ret_name is not None
                        else rec.get("candidate_id")
                    )
                if "sibling_name" not in rec:
                    rec["sibling_name"] = (
                        _resolve_ret_name(_ret_bot_state, rec.get("sibling_id"))
                        if _resolve_ret_name is not None
                        else rec.get("sibling_id")
                    )

                # PR#140 2nd /code-review findings 2+3 (Cycle 2d, AC-2): round
                # every numeric field to _MATERIAL_CHANGE_ROUND_NDIGITS BEFORE
                # the reuse-vs-fresh decision AND before explain_recommendation
                # sees it -- so the LLM never cites a number more precise than
                # what gets persisted/rendered (closes finding 3's stale-
                # citation window), and a stable pair's rounded evidence is
                # byte-identical night-to-night even though build_recommendations
                # legitimately returns slightly different raw floats each run
                # (restores finding 2's reuse spend savings). _round_floats is
                # pure (returns a NEW structure) -- reassign onto recs[_i] so
                # persist_recommendations(recs) sees the rounded values
                # regardless of which branch below runs; a rebind of the
                # loop-local `rec` name alone would not propagate. n_obs (int)
                # passes through _round_floats unrounded, as always.
                rec = _round_floats(rec, _MATERIAL_CHANGE_ROUND_NDIGITS)
                recs[_i] = rec

                reused_explanation = _retirement_find_reusable_prior_explanation(rec, _prior_map)
                if reused_explanation is not None:
                    rec["explanation"] = reused_explanation
                    continue
                try:
                    rec["explanation"] = explain_recommendation(rec)
                except Exception as exc:
                    # Defense-in-depth: explain_recommendation is D-1/never-
                    # raises by its own contract, but a violation must never
                    # take down the whole tick or leak the raw exception.
                    _daemon_log.warning(
                        "Retirement explainer failed for %s: %s",
                        rec.get("candidate_id"),
                        type(exc).__name__,
                    )
                    rec["explanation"] = None
        persist_recommendations(recs)
        _daemon_log.info("Retirement recommender tick complete: %d recommendation(s).", len(recs))
    except Exception as exc:
        _daemon_log.error("Retirement recommender tick worker failed: %s", type(exc).__name__)


def _run_retirement_recommender_tick() -> None:
    """Non-blocking daily off-hours wrapper for the retirement recommender tick.

    Spawns a daemon thread so the scheduler thread returns immediately — the
    tick never blocks the 1-minute execution path (arch constraint 1).
    """
    import threading

    t = threading.Thread(
        target=_retirement_recommender_tick_worker,
        daemon=True,
        name="retirement-recommender-tick",
    )
    t.start()


def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    schedule.every().minute.at(":00").do(_refresh_account_totals)
    schedule.every().day.at("02:00").do(_run_trigger_retention)
    # Component 7+8: daily off-hours lens pipeline — Market Prism summary (CYCLE4-BRIEF.md).
    # Runs at 03:00 (off-hours) so it never overlaps the live market-hours execution path.
    schedule.every().day.at("03:00").do(_run_lens_pipeline)
    # Strategy Incubation Gate daily tick — staggered 30 minutes after the lens
    # pipeline slot so the two off-hours jobs never contend for the same minute.
    schedule.every().day.at("03:30").do(_run_incubation_tick)
    # Retirement Recommender daily tick — staggered 15 minutes after the
    # incubation slot so all three off-hours jobs run without same-minute
    # contention.
    schedule.every().day.at("03:45").do(_run_retirement_recommender_tick)
    while True:
        schedule.run_pending()
        time.sleep(1)


# AC-16 s3-ux live-render finding #1: all 6 named statuses must map to a
# pairwise visually-distinct status-pill class -- PAUSED_RECONCILIATION in
# particular must never collapse into the same class as a mundane fresh
# SHADOW sleeve. Unrecognized statuses fail safe to "standby".
_SLEEVE_STATUS_BADGE_CLASSES = {
    "SHADOW": "standby",
    "PAPER": "armed",
    "LIVE": "triggered",
    "BENCHED": "sleeve-benched",
    "PAUSED_RECONCILIATION": "sleeve-paused",
    "stale": "sleeve-stale",
}


def _sleeve_status_badge_class(status: str) -> str:
    return _SLEEVE_STATUS_BADGE_CLASSES.get(status, "standby")


def _build_sleeves_panel_context() -> list[dict]:
    """Assemble per-sleeve panel rows for dashboard() (AC-16): status,
    capital, ledger cash (vs the static capital_usd -- "cash ledger vs
    broker truth"; the broker-truth-mismatch signal itself is the existing
    sleeve.status column, PAUSED_RECONCILIATION already meaning "known
    mismatch"), and per-rule mode/today-lifetime fire counts/realized P&L.
    Never raises -- a read failure on one sleeve/rule degrades that row
    rather than 500ing the whole dashboard (dashboard-truth rule).

    Ledger cash, the sleeve-level realized P&L, and per-rule realized P&L are
    all derived with ZERO new schema from sleeve_orders/sleeve_fills:
    sleeves.ledger.reconstruct_from_history (sleeve-wide) for cash + the
    sleeve's true realized figure, and sleeves.ledger.attribute_realized_fills
    for the per-rule split -- BUY-side attribution (realized P&L belongs to
    the entry rule whose lots the sell closed, not the rule that fired the
    sell), so a defensive rule exiting another rule's position no longer
    collapses every rule to $0.00 (audit finding #7). Per-rule values sum to
    the sleeve figure by construction. On fold failure the value is None and
    the template renders an explicit "n/a" marker -- $0.00 is a value claim,
    never a degraded state.

    Bench/stale visibility (AC-11/AC-16): per-rule benched comes from
    sleeves.rules.limits.is_rule_benched (the engine's OWN read API -- bench
    is keyed by ET trading day in sleeve_runtime, so the flag auto-clears
    next trading day in lockstep with the engine's auto re-arm) and per-rule
    stale from the STALE_NO_BARS_KEY runtime flag the tick writes. Both are
    read-only lookups of engine-computed state -- the panel never reruns the
    brake. The sleeve pill applies the ratified display precedence
    (2026-07-09): PAUSED_RECONCILIATION > BENCHED (any rule benched) > stale
    (EVERY enabled rule stale) > base status -- display-level only, the DB
    sleeve.status is never rewritten by a badge.
    """
    from sleeves import ledger as sleeve_ledger  # noqa: PLC0415
    from sleeves.rules import limits as sleeve_limits  # noqa: PLC0415

    panel_sleeves: list[dict] = []
    try:
        sleeve_rows = database.get_all_sleeves()
    except Exception:
        return panel_sleeves

    now_utc = datetime.now(UTC)
    today_str = datetime.now(_ET).strftime("%Y-%m-%d")
    for sleeve in sleeve_rows:
        sleeve_id = sleeve.get("id")
        capital_usd = sleeve.get("capital_usd") or 0.0
        try:
            rule_rows = (
                database.get_sleeve_rules_for_sleeve(sleeve_id) if sleeve_id is not None else []
            )
        except Exception:
            rule_rows = []

        try:
            order_history = (
                database.get_sleeve_order_history(sleeve_id) if sleeve_id is not None else []
            )
        except Exception:
            order_history = []

        try:
            ledger_state = sleeve_ledger.reconstruct_from_history(capital_usd, order_history)
            ledger_cash_usd = ledger_state.cash_usd
            sleeve_realized_pnl_usd = ledger_state.realized_pnl_usd
        except Exception:
            ledger_cash_usd = capital_usd
            sleeve_realized_pnl_usd = None

        try:
            realized_by_rule = {}
            for record in sleeve_ledger.attribute_realized_fills(order_history):
                realized_by_rule[record.opening_rule_id] = (
                    realized_by_rule.get(record.opening_rule_id, 0.0) + record.realized_delta_usd
                )
        except Exception:
            # None (not {}): the fold FAILED, which must render as "n/a" per
            # rule -- an empty dict would render every rule as a $0.00 value
            # claim, the exact audit-#7 bug this replaces.
            realized_by_rule = None

        rules_panel = []
        any_rule_benched = False
        enabled_stale_flags: list[bool] = []
        for rule in rule_rows:
            rule_id = rule.get("id")
            # COUNT accessors, never len(limited rows): the fires accessor's
            # default limit=100 silently capped "lifetime" (audit #14), and
            # prefix-comparing the ET day against UTC-stored fired_at
            # misattributed evening fires (audit #15 -- the day accessor
            # converts the ET day to its exact UTC window).
            try:
                lifetime_fires = (
                    database.get_sleeve_rule_fire_count(rule_id) if rule_id is not None else 0
                )
                today_fires = (
                    database.get_fire_count_for_rule_on_day(rule_id, today_str)
                    if rule_id is not None
                    else 0
                )
            except Exception:
                lifetime_fires = 0
                today_fires = 0

            rule_realized_pnl = (
                realized_by_rule.get(rule_id, 0.0) if realized_by_rule is not None else None
            )

            try:
                benched = rule_id is not None and sleeve_limits.is_rule_benched(
                    rule_id, now_utc=now_utc
                )
            except Exception:
                benched = False
            try:
                stale = rule_id is not None and (
                    database.get_sleeve_runtime(rule_id, sleeve_limits.STALE_NO_BARS_KEY) == "1"
                )
            except Exception:
                stale = False
            if benched:
                any_rule_benched = True
            if rule.get("enabled"):
                enabled_stale_flags.append(stale)

            rules_panel.append(
                {
                    "id": rule_id,
                    "name": rule.get("name", ""),
                    "mode": rule.get("mode", ""),
                    "today_fires": today_fires,
                    "lifetime_fires": lifetime_fires,
                    "realized_pnl_usd": rule_realized_pnl,
                    "benched": benched,
                    "stale": stale,
                }
            )

        status = sleeve.get("status", "")
        badge_class = _sleeve_status_badge_class(status)
        if status != "PAUSED_RECONCILIATION":
            if any_rule_benched:
                badge_class = _SLEEVE_STATUS_BADGE_CLASSES["BENCHED"]
            elif enabled_stale_flags and all(enabled_stale_flags):
                badge_class = _SLEEVE_STATUS_BADGE_CLASSES["stale"]

        panel_sleeves.append(
            {
                "id": sleeve_id,
                "name": sleeve.get("name", ""),
                "status": status,
                "status_badge_class": badge_class,
                "capital_usd": sleeve.get("capital_usd"),
                "ledger_cash_usd": ledger_cash_usd,
                "realized_pnl_usd": sleeve_realized_pnl_usd,
                "rules": rules_panel,
            }
        )
    return panel_sleeves


def _atlas_cache_health() -> dict:
    """Read-only cache-row age + availability summary for the Atlas/Front
    Runner weekly-cached catalog (audit MEDIUM-2). Reads directly from the
    atlas_cache SQLite DB (advisors/atlas_cache.py's schema, opened read-only)
    -- never calls the live loader/fetch path from this request handler (the
    dashboard is never a live-trade-action surface, and the loader's own
    weekly-cache refresh is triggered elsewhere, never from a Flask route).
    Never raises -- returns a structural "no cache row yet"-style reason
    rather than fabricating an error cause.
    """
    import sqlite3  # noqa: PLC0415 — stdlib, lazy for locality

    db_path = os.environ.get("ATLAS_CACHE_DB_PATH", "alphabot_atlas_cache.db")
    collection = "captplanet.strategies"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {"available": False, "age_days": None, "reason": "no_cache_db_yet"}
    try:
        row = conn.execute(
            "SELECT fetched_at FROM atlas_cache WHERE collection = ?", (collection,)
        ).fetchone()
    except sqlite3.OperationalError:
        return {"available": False, "age_days": None, "reason": "no_cache_table_yet"}
    finally:
        conn.close()

    if row is None:
        return {"available": False, "age_days": None, "reason": "no_cache_row_yet"}

    try:
        fetched_at = datetime.fromisoformat(row[0])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=ZoneInfo("UTC"))
        age_days = (datetime.now(ZoneInfo("UTC")) - fetched_at).total_seconds() / 86400.0
        return {"available": True, "age_days": round(age_days, 1), "reason": None}
    except (ValueError, TypeError):
        return {"available": False, "age_days": None, "reason": "unparseable_fetched_at"}


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

        def _safe_analytics(fn, *args, coerce_none: bool = True, **kwargs):
            try:
                result = fn(*args, **kwargs)
                if not isinstance(result, dict):
                    return (
                        {"if_held": 0.0, "dry_run": 0.0}
                        if coerce_none
                        else {
                            "if_held": None,
                            "dry_run": None,
                        }
                    )
                if not coerce_none:
                    return result
                return {k: (v if v is not None else 0.0) for k, v in result.items()}
            except Exception:
                return (
                    {"if_held": 0.0, "dry_run": 0.0}
                    if coerce_none
                    else {
                        "if_held": None,
                        "dry_run": None,
                    }
                )

        if "_cr" not in _s:
            _s["_cr"] = _safe_analytics(
                analytics.get_symphony_cumulative_return, _sym_dict, _s, trading_day=_dash_today
            )
        if "_tc" not in _s:
            # F-016: _tc feeds the per-card Today cells only (templates/index.html),
            # which now have their own None-aware guard -- do NOT coerce a genuine
            # None to 0.0 here, that fabricates a false "+0.0%" for missing data.
            _s["_tc"] = _safe_analytics(
                analytics.get_symphony_today_change,
                _sym_dict,
                _s,
                trading_day=_dash_today,
                coerce_none=False,
            )
        if "_mdd" not in _s:
            _s["_mdd"] = _safe_analytics(
                analytics.get_symphony_max_drawdown, _sym_dict, _s, trading_day=_dash_today
            )

    active_syms = [
        s
        for s in symphonies
        if s.get("armed") or s.get("tp_armed") or s.get("para_armed") or s.get("triggered")
    ]
    standby_syms = [
        s
        for s in symphonies
        if not (s.get("armed") or s.get("tp_armed") or s.get("para_armed") or s.get("triggered"))
    ]

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
            cvar_diagnostic = database.read_cvar_diagnostic_for_symphony(_first_sym_id)
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
        sleeves=_build_sleeves_panel_context(),
        atlas_cache_health=_atlas_cache_health(),
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
        if (s.get("armed") or s.get("tp_armed") or s.get("para_armed")) and not s.get("triggered")
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
        # Option A: the WINDOWED guard alpha (default window) + its window echo feed the
        # SSR hero headline so first paint matches the picker; account_all_time_cr is the
        # SEPARATE, non-windowed "Account · all-time" reference.
        "guard_alpha": ps.get("guard_alpha"),
        "window": ps.get("window"),
        "account_all_time_cr": ps.get("account_all_time_cr"),
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


# Option A (PM live gate): the hero loads pre-windowed at this default window so the
# headline GUARD ALPHA on first paint equals /api/strip/<default> — no 67-vs-29 jump on
# the first picker click. Must match the picker's active button + label
# (templates/index.html: window-30d active, label "30d").
_DEFAULT_HERO_WINDOW = "30d"


def _project_today_change_floor(vw_tc: dict) -> dict:
    """Tier-2 "no account totals" floor's Today's Change projection.

    DE-HELD-BASIS-001 (FINDING-2/AC-5): when neither a warm account-totals
    cache nor a last-good snapshot is available, the Tier-2 floor (both the
    live branch in _compute_portfolio_strip and the frozen-snapshot branch in
    get_state()) falls back to rendering the value-weighted portfolio TC
    directly -- analytics's own portfolio-today-change aggregator, called
    with include_paired_guard_delta=True. Its raw dry_run is a dry-run-ONLY-
    membership average, which mismatches if_held's full-membership average
    on a coverage-gap day -- producing a phantom delta even when true guard
    divergence is zero. This helper re-derives dry_run from if_held + the
    paired guard_delta_vw (already coverage-scaled by
    analytics._value_weighted_portfolio) so both figures share full
    membership. if_held is always passed through unchanged (the floor's own
    contract, AC-6) -- only dry_run is re-derived.

    Deliberately a module-level app.py helper, not an analytics.py function:
    it is a pure view-projection with no account-scaling math (unlike
    analytics.get_portfolio_today_change_account_basis, which genuinely
    computes invested_frac and scales by it) — it belongs with its 2
    call sites, not in the analytics module. Being an app.py-owned
    function (not an `analytics.` attribute) also means tests that
    `patch.object(app_module, "analytics", MagicMock())` never touch it —
    unlike an analytics.py function, which would silently become an
    unconfigured MagicMock under any such mock that reaches the floor path.

    Structural guarantee: the internal "guard_delta_vw" key must never leak
    into a public JSON response (F6). This helper always constructs a FRESH
    {"if_held", "dry_run"} dict -- it never returns vw_tc itself, so the
    result is exactly 2 keys regardless of what vw_tc carries.

    Args:
        vw_tc: value-weighted portfolio TC dict, normally the output of
               analytics's portfolio-today-change aggregator called with
               include_paired_guard_delta=True. May carry an internal
               "guard_delta_vw" key -- stripped here.

    Returns:
        {"if_held": vw_tc.get("if_held"), "dry_run": ...} -- if_held + the
        paired guard_delta_vw when both are present, else vw_tc's raw
        if_held/dry_run passthrough (honest degradation, e.g. zero paired
        coverage -> dry_run=None).

    Note: deliberately does NOT float()-cast guard_delta or if_held, unlike
    analytics.get_portfolio_today_change_account_basis -- the values already
    come back as floats from analytics.get_portfolio_today_change, and
    adding a cast here would be a behavior change, not a pure extraction.
    """
    # `is not None`, not a truthiness check -- a genuine 0.0 guard delta must
    # still trigger the re-derivation. This is the same not-None DISCIPLINE
    # (not a truthiness check) that
    # analytics.get_portfolio_today_change_account_basis applies to its own
    # paired-delta guard -- not an identical guard: that sibling guards on
    # the delta alone (if_held is handled by an earlier return) and
    # float()-casts + scales by invested_frac; this helper guards on
    # guard_delta AND if_held together, with no cast/scale.
    guard_delta = vw_tc.get("guard_delta_vw")
    if guard_delta is not None and vw_tc.get("if_held") is not None:
        return {"if_held": vw_tc["if_held"], "dry_run": vw_tc["if_held"] + guard_delta}
    return {"if_held": vw_tc.get("if_held"), "dry_run": vw_tc.get("dry_run")}


def _compute_portfolio_strip(
    bot_state: dict, trading_day: str | None = None, conn: sqlite3.Connection | None = None
) -> dict:
    """Compute portfolio_strip from bot_state using analytics helpers.

    Shared by get_api_state_dict() (Jinja render path) and get_state() (JSON
    poll path) so both paths emit identical portfolio_strip shape.  This closes
    the systemic 0.00% everywhere defect (FP-T1-01).

    Option A: the windowed hero metrics (guard_alpha + window echo) are computed via
    analytics.compute_windowed_portfolio_strip at _DEFAULT_HERO_WINDOW — the SAME path
    /api/strip/<window> uses — so the default hero matches the picker's first click. The
    account-lifetime CR (~Composer simple_return) is surfaced SEPARATELY as
    account_all_time_cr: it carries no window label and never windows.

    conn: F-1 — optional pre-opened read-only connection, forwarded to the
    portfolio CR/TC/MDD helpers below (each of which loops every symphony) so
    the whole call shares ONE connection instead of opening one per symphony
    per helper. This function never opens/closes conn itself — the caller
    owns its lifecycle; None here just falls back to today's per-call behavior.
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
        # DE-HELD-BASIS-001 F4 (PR #125 review): no current_return_is_reconstructed
        # key threaded into the sym_dict above — analytics.get_symphony_today_change
        # now reads the marker from bot_state_entry (the real bot_state[k] dict
        # passed below, already carrying BL-9's marker) as the PRIMARY source, so
        # this per-symphony threading is redundant by construction (removed, was
        # added in the original cycle before the F4 design pivot).

    # Use cached Composer account-level value when available; per-symphony sum as fallback.
    # Use .get() (single call) rather than __contains__ + __getitem__ to eliminate the
    # TOCTOU window: _notify_cycle_complete() can call mark_stale() between the two calls,
    # causing __contains__ to return True but __getitem__ to raise KeyError.
    _cached_portfolio_value = _account_totals_cache.get("portfolio_value")
    if _cached_portfolio_value is not None:
        account_value = _cached_portfolio_value
    else:
        # Tier 1 — last-good present: use the retained cash-inclusive account total
        # rather than falling straight to the cash-EXCLUDED per-symphony sum (mirrors
        # the frozen path's account_value fallback).
        _lg_portfolio_value = _account_totals_last_good.get("portfolio_value")
        if _lg_portfolio_value is not None:
            account_value = _lg_portfolio_value
        else:
            account_value = sum(
                v.get("current_value") or 0.0 for v in bot_state.values() if isinstance(v, dict)
            )

    try:
        # Use .get() for all cache reads below to eliminate TOCTOU between __contains__
        # and __getitem__ — _StaleFlagDict.mark_stale() can fire between the two calls
        # and cause __getitem__ to raise KeyError even when __contains__ returned True.
        # Hoisted: both the CR and TC account-basis helpers need this sum, and it is
        # cheap (a single pass over symphonies_list).  Computing it once here avoids
        # the duplicate that previously lived inside the if _cached_cr branch only,
        # which left it out of scope for the TC block.
        _symphony_value_sum = sum(s.get("value") or 0.0 for s in symphonies_list)

        # Track whether the live strip is using last-good (stale) account totals so
        # the caller can stamp account_basis_stale on the returned portfolio_strip.
        _live_basis_stale = False

        _cached_cr = _account_totals_cache.get("portfolio_cr")
        if _cached_cr is not None:
            # B-1 fix: put Bot (dry_run) on the same account basis as Held (if_held).
            # Held = Composer simple_return (cash-inclusive denominator).
            # Bot = VW per-symphony guard divergence scaled to account basis so that
            # guard_alpha = dry_run - if_held is a scope-clean apples-to-apples delta.
            # guard_delta is measured on the VW basis first (dry_run and if_held share
            # the same symphony-value denominator), then scaled by invested_frac.
            _vw_cr = analytics.get_portfolio_cumulative_return(
                symphonies_list, bot_state, trading_day=trading_day, conn=conn
            )
            cumulative_return: dict | None = (
                analytics.get_portfolio_cumulative_return_account_basis(
                    _vw_cr,
                    _cached_cr,
                    account_value,
                    _symphony_value_sum,
                )
            )
        else:
            # Tier 1 — last-good present: use retained snapshot so a transient Composer
            # timeout doesn't silently flip to unlabelled VW values.
            _lg_cr = _account_totals_last_good.get("portfolio_cr")
            if _lg_cr is not None:
                _vw_cr = analytics.get_portfolio_cumulative_return(
                    symphonies_list, bot_state, trading_day=trading_day, conn=conn
                )
                cumulative_return = analytics.get_portfolio_cumulative_return_account_basis(
                    _vw_cr, _lg_cr, account_value, _symphony_value_sum
                )
                _live_basis_stale = True
            else:
                # Tier 2 — no last-good: fall back to VW (label applied below).
                cumulative_return = analytics.get_portfolio_cumulative_return(
                    symphonies_list, bot_state, trading_day=trading_day, conn=conn
                )

        # D-01 / B-2 fix: use the Composer-sourced today-change (includes cash in
        # denominator) when available, and put Bot on the same account basis so that
        # guard_alpha = dry_run - if_held is zero when no guard has fired.
        # Previously: if_held = _cached_tc (account basis, cash-inclusive) but
        # dry_run = VW symphony sum (cash-excluded) — different denominators produced
        # phantom alpha even when all symphonies were bot == held.
        # DE-HELD-BASIS-001 F6 CRITICAL INTERACTION TRAP (PR #125 review): all THREE
        # get_portfolio_today_change call sites below (Tier-0/Tier-1/Tier-2) must pass
        # include_paired_guard_delta=True explicitly now that the function's own
        # default is False (F6) — the Tier-2 floor's paired re-derivation further
        # below reads vw_tc.get("guard_delta_vw") and silently falls back to a raw
        # passthrough if any one of the three omits the opt-in.
        _cached_tc = _account_totals_cache.get("portfolio_tc")
        if _cached_tc is not None:
            _vw_tc = analytics.get_portfolio_today_change(
                symphonies_list,
                bot_state,
                trading_day=trading_day,
                conn=conn,
                include_paired_guard_delta=True,
            )
            today_change: dict = analytics.get_portfolio_today_change_account_basis(
                _vw_tc,
                _cached_tc,
                account_value,
                _symphony_value_sum,
            )
        else:
            # Tier 1 — last-good present.
            _lg_tc = _account_totals_last_good.get("portfolio_tc")
            if _lg_tc is not None:
                _vw_tc = analytics.get_portfolio_today_change(
                    symphonies_list,
                    bot_state,
                    trading_day=trading_day,
                    conn=conn,
                    include_paired_guard_delta=True,
                )
                today_change = analytics.get_portfolio_today_change_account_basis(
                    _vw_tc, _lg_tc, account_value, _symphony_value_sum
                )
                _live_basis_stale = True
            else:
                # Tier 2 — no last-good: fall back to VW (label applied below).
                _vw_tc_floor = analytics.get_portfolio_today_change(
                    symphonies_list,
                    bot_state,
                    trading_day=trading_day,
                    conn=conn,
                    include_paired_guard_delta=True,
                )
                # DE-HELD-BASIS-001 (FINDING-2/AC-5): re-derive dry_run from the paired
                # guard_delta_vw so it matches if_held's full membership (see
                # _project_today_change_floor's docstring).
                today_change = _project_today_change_floor(_vw_tc_floor)

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
        _cached_mdd = _account_totals_cache.get("portfolio_mdd")
        if _cached_mdd is not None:
            max_drawdown: dict = {
                "if_held": abs(_cached_mdd),
                "dry_run": analytics.get_portfolio_max_drawdown(
                    symphonies_list, bot_state, trading_day=trading_day, conn=conn
                ).get("dry_run"),
            }
        else:
            max_drawdown = analytics.get_portfolio_max_drawdown(
                symphonies_list, bot_state, trading_day=trading_day, conn=conn
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
        # AC-4c: capture the shadow DATES (not just the returns) so the strip carries
        # hist_dates. _build_meta derives insufficient_history = len(hist_dates) < 30
        # from this; without it the SSR render path always saw hist_dates=[] and
        # flagged EVERY portfolio as <30d — making the MAX-DD Bot +0.00% read as a
        # guard win (the empty-series artifact) even with ample history.
        hist_dates: list[str] = []
        hist_bot: list[float] = []
        hist_held: list[float] = []
        _shadow_result = analytics.get_portfolio_daily_returns_from_shadow()
        if _shadow_result is not None:
            _shadow_dates, _port_daily_returns = _shadow_result
            vol_bot = analytics.compute_portfolio_annualized_vol(_port_daily_returns)
            hist_dates = list(_shadow_dates)

        # Prefer the REAL (bot, held) series for the initial hero chart so the dashed
        # "If held" line is genuine on first paint (not a verbatim Bot copy — F3). Falls
        # back silently to an empty series; the windowed /api/hero-chart route refreshes
        # it on any picker click regardless.
        try:
            _bh = analytics.get_portfolio_bot_and_held_daily_returns()
            if _bh is not None:
                _bh_dates, _bh_bot, _bh_held = _bh
                if not hist_dates:
                    hist_dates = list(_bh_dates)
                _rb = 1.0
                _rh = 1.0
                for _b, _h in zip(_bh_bot, _bh_held):
                    _rb *= 1.0 + _b / 100.0
                    _rh *= 1.0 + _h / 100.0
                    hist_bot.append(round((_rb - 1.0) * 100.0, 4))
                    hist_held.append(round((_rh - 1.0) * 100.0, 4))
        except Exception:
            _daemon_log.error("_compute_portfolio_strip bot/held series failed", exc_info=True)

        # Derive data_as_of from the actual data timestamp, not the server render clock.
        # Falls back to datetime.now() if no cycle timestamp is available.
        # The engine writes last_successful_cycle_at at the TOP LEVEL of bot_state
        # (alpha_bot_execution.py:948/1092/1878) — read it directly, not via a per-sym loop.
        _cycle_ts = bot_state.get("last_successful_cycle_at")
        if _cycle_ts:
            try:
                _dt = datetime.fromisoformat(_cycle_ts.replace("Z", "+00:00"))
                # The engine writes last_successful_cycle_at as a TZ-AWARE isoformat string
                # (get_current_et() returns datetime.now(ZoneInfo("America/New_York")) —
                # alpha_bot_execution.py:437-442).  The tzinfo-is-None branch below covers
                # older DB rows written before the aware-datetime fix.
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=_ET)
                _dt_et = _dt.astimezone(_ET)
                _data_as_of = _dt_et.strftime("%H:%M ET")
            except Exception:
                _data_as_of = datetime.now(_ET).strftime("%H:%M ET")
        else:
            _data_as_of = datetime.now(_ET).strftime("%H:%M ET")

        _strip = {
            "today_change": today_change,
            "cumulative_return": cumulative_return,
            "max_drawdown": max_drawdown,
            "account_value": account_value,
            "vol_bot": vol_bot,
            "vol_held": vol_held,
            "hist_dates": hist_dates,
            "hist_bot": hist_bot,
            "hist_held": hist_held,
            # F-026: hist_bot/hist_held here are genuinely shadow_history-sourced
            # (analytics.get_portfolio_bot_and_held_daily_returns) -- must be set
            # explicitly or _build_meta's ps.get("hist_source", "post_mortem")
            # silently serves the wrong default.
            "hist_source": "shadow_history",
            "data_as_of": _data_as_of,
        }

        # Tier 2 honest-floor marker: fires when EITHER field has no basis at all (no
        # cache, no last-good) — a missing TC must not mask an independently-missing CR
        # and vice versa (each field's own cache+last-good state is checked). `is None`
        # (not a falsy check) — a real last-good value of 0.0 (a genuine flat-day
        # reading) must count as present, not fully-missing.
        _tc_fully_missing = (
            _cached_tc is None and _account_totals_last_good.get("portfolio_tc") is None
        )
        _cr_fully_missing = (
            _cached_cr is None and _account_totals_last_good.get("portfolio_cr") is None
        )
        if _tc_fully_missing or _cr_fully_missing:
            _strip["basis"] = "value_weighted"
        # Tier 1 stale stamps: using last-good account totals for either field. Falls
        # back to a fresh ET timestamp string when _account_totals_last_success_at was
        # never set by a real refresh (mirrors the frozen path's fallback).
        if _live_basis_stale:
            _strip["account_basis_stale"] = True
            _strip["account_basis_as_of"] = _account_totals_last_success_at or datetime.now(
                _ET
            ).strftime(_ACCOUNT_BASIS_TS_FMT)

        # Option A: surface the Composer account-lifetime CR as its OWN non-windowed stat
        # ("Account · all-time"). It is the cash-inclusive simple_return scalar — there is
        # no daily account series to window it on — so it stays fixed across picker windows
        # and never carries the window label. Numeric-guarded at the serialization boundary.
        _acct_cr = _account_totals_cache.get("portfolio_cr")
        if isinstance(_acct_cr, (int, float)):
            _strip["account_all_time_cr"] = _acct_cr

        # Option A: load the hero pre-windowed at the default window so the headline GUARD
        # ALPHA equals /api/strip/<default> (same compute_windowed_portfolio_strip path,
        # same symphonies_list) — no value jump on the first picker click. guard_alpha +
        # window are the windowed hero metric + its honest label.
        try:
            _default = analytics.compute_windowed_portfolio_strip(
                symphonies_list, bot_state, window=_DEFAULT_HERO_WINDOW, conn=conn
            )
            if isinstance(_default, dict):
                _ga = _default.get("guard_alpha")
                _strip["guard_alpha"] = _ga if isinstance(_ga, (int, float)) else None
                _win = _default.get("window", _DEFAULT_HERO_WINDOW)
                _strip["window"] = _win if isinstance(_win, str) else _DEFAULT_HERO_WINDOW
                # DE-CLOSED-BOUNCE-001 (revise round): windowed_cumulative_return is
                # NOT currently consumed client-side — F-014 removed updateComparison
                # Rows' only read of it, and the revise round's rows-array gate
                # (static/index.js) means no comparison row is ever sourced from a
                # windowed-strip payload at all. Retained here only for response-shape
                # parity with /api/strip/<window>'s own output.
                _wcr = _default.get("cumulative_return")
                if isinstance(_wcr, dict):
                    _strip["windowed_cumulative_return"] = _wcr
        except Exception:
            _daemon_log.error("_compute_portfolio_strip default-window strip failed", exc_info=True)

        return _strip
    except Exception as _exc:
        _daemon_log.error(
            "_compute_portfolio_strip failed — portfolio strip will be null: %s",
            _exc,
            exc_info=True,
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

    # F-1: function-local shared read-only connection for the ONE
    # _compute_portfolio_strip call below (each of its portfolio CR/TC/MDD
    # helpers loops every symphony internally, opening its own connection
    # per symphony without this) — opened here, closed in the finally, never
    # a module-global. Falls back to None (today's per-call behavior) if the
    # shared connection itself fails to open.
    try:
        _shadow_conn = sqlite3.connect(
            f"file:{analytics._get_shadow_db_file()}?mode=ro", uri=True, timeout=10.0
        )
    except Exception:
        _shadow_conn = None
    try:
        portfolio_strip = _compute_portfolio_strip(bot_state, conn=_shadow_conn)
    finally:
        if _shadow_conn is not None:
            _shadow_conn.close()

    return {
        "bot_state": bot_state,
        "is_locked": is_locked,
        "port_state": port_state,
        "exit_authority": exit_authority,
        "daemon_started_at": _DAEMON_STARTED_AT,
        "portfolio_strip": portfolio_strip,
    }


@app.route("/api/events")
def sse_events():
    """Server-Sent Events endpoint — streams cycle-complete notifications (AC-2).

    Auth-gated by _auth_before_request like all /api/ routes.
    Each connected client gets a Queue; _notify_cycle_complete() fans out to all.
    Heartbeat comment every 15 s keeps the connection alive.
    """

    client_q: queue.Queue = queue.Queue()

    def generate():
        with _sse_clients_lock:
            _sse_clients.append(client_q)
        try:
            while True:
                try:
                    msg = client_q.get(timeout=15)  # 15 s heartbeat cadence
                    yield f"event: {msg}\ndata: {{}}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            # PEP 342: close() throws GeneratorExit into the generator; a `finally`
            # block runs without any explicit catch, so no separate handler is needed.
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(client_q)
                except ValueError:
                    pass

    response = app.response_class(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/state")
def get_state():
    try:
        _ro_conn = database.get_ro_connection()
        market_state = get_market_state(datetime.now(_ET))

        # AC-P2.12.2: additive fields — computed once, merged into every response branch.
        _api_state = get_api_state_dict()
        _additive_env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
        _additive_live_mode = _additive_env.get("LIVE_EXECUTION", "False").lower() in (
            "true",
            "1",
            "yes",
        )
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
                        _running *= 1.0 + _d / 100.0
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
                            _running_bot *= 1.0 + _bot_d / 100.0
                            _running_held *= 1.0 + _held_d / 100.0
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
                # F-1 (frozen branch): ONE shared read-only connection for BOTH the
                # per-symphony TC/CR/MDD loop right below AND the 3 portfolio-level
                # analytics calls further down this same frozen-snapshot block —
                # mirrors the live branch's fix (this is the branch most likely to
                # have served the PM's original off-hours/Saturday repro).
                # Function-local/per-request scope, closed once both sections are
                # done (below, after the account-totals try/except). Falls back to
                # None (each analytics call opens its own connection, today's
                # behavior) if the shared connection itself fails to open.
                try:
                    _frozen_shadow_conn = sqlite3.connect(
                        f"file:{analytics._get_shadow_db_file()}?mode=ro", uri=True, timeout=10.0
                    )
                except Exception:
                    _frozen_shadow_conn = None
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
                                _sym_dict,
                                _sym,
                                trading_day=_snap_trading_day,
                                conn=_frozen_shadow_conn,
                            )
                        except (KeyError, TypeError, ValueError):
                            _sym["_tc"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_cr"] = analytics.get_symphony_cumulative_return(
                                _sym_dict,
                                _sym,
                                trading_day=_snap_trading_day,
                                conn=_frozen_shadow_conn,
                            )
                        except (KeyError, TypeError, ValueError):
                            _sym["_cr"] = {"if_held": None, "dry_run": None}
                        try:
                            _sym["_mdd"] = analytics.get_symphony_max_drawdown(
                                _sym_dict,
                                _sym,
                                trading_day=_snap_trading_day,
                                conn=_frozen_shadow_conn,
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
                            # DE-HELD-BASIS-001 F5(b) (PR #125 review): _snap_bot_state is
                            # hand-built, NOT the real bot_state entry — the ONE shape F4's
                            # marker-precedence pivot does not cover for free, so the marker
                            # must be added explicitly here.
                            "current_return_is_reconstructed": _sym_r.get(
                                "current_return_is_reconstructed", False
                            ),
                        }
                # Derive data_as_of from the snapshot's own capture time.
                # captured_at_et is written as "%H:%M:%S ET" (e.g. "16:00:01 ET");
                # reformat to "%H:%M ET" to match the live-path format.
                # Fall back to snapshot.data_as_of (may be None for legacy snapshots).
                _snap_captured = snapshot.get("captured_at_et") or ""
                try:
                    # Strip the " ET" suffix, parse HH:MM:SS or HH:MM, reformat.
                    _cap_time_str = _snap_captured.replace(" ET", "").strip()
                    _cap_parts = _cap_time_str.split(":")
                    _snap_data_as_of = f"{_cap_parts[0]}:{_cap_parts[1]} ET"
                except Exception:
                    _snap_data_as_of = snapshot.get("data_as_of")

                try:
                    # Compute symphony value sum for account-basis scaling (mirrors live path).
                    _snap_symphony_value_sum = sum(
                        s.get("value", 0.0) or 0.0 for s in _snap_symphonies_list
                    )

                    # Resolve account totals with two-tier stale-cache fallback. TC, CR,
                    # and value are each resolved INDEPENDENTLY (mirrors the live path's
                    # per-field checks) — a missing field must never collaterally degrade
                    # an independently-warm sibling field.
                    # Use .get() to avoid TOCTOU: mark_stale() can fire between calls.
                    _snap_account_tc = _account_totals_cache.get("portfolio_tc")
                    _snap_tc_stale = False
                    if _snap_account_tc is None and _account_totals_last_good:
                        _snap_account_tc = _account_totals_last_good.get("portfolio_tc")
                        _snap_tc_stale = _snap_account_tc is not None

                    _snap_account_cr = _account_totals_cache.get("portfolio_cr")
                    _snap_cr_stale = False
                    if _snap_account_cr is None and _account_totals_last_good:
                        _snap_account_cr = _account_totals_last_good.get("portfolio_cr")
                        _snap_cr_stale = _snap_account_cr is not None

                    _snap_cached_value = _account_totals_cache.get("portfolio_value")
                    if _snap_cached_value is None and _account_totals_last_good:
                        _snap_cached_value = _account_totals_last_good.get("portfolio_value")

                    # Tier 1 — fires when EITHER field fell back to last-good.
                    _snap_basis_stale = _snap_tc_stale or _snap_cr_stale
                    _snap_basis_as_of = (
                        (
                            _account_totals_last_success_at
                            or datetime.now(_ET).strftime(_ACCOUNT_BASIS_TS_FMT)
                        )
                        if _snap_basis_stale
                        else None
                    )

                    # VW intermediates (same calls as live path). include_paired_guard_delta=
                    # True (DE-HELD-BASIS-001 F5(a)/F6, PR #125 review): the Tier-2 floor
                    # branch below needs the coverage-scaled guard_delta_vw for its own
                    # paired re-derivation, same as the live path's three call sites.
                    _snap_vw_tc = analytics.get_portfolio_today_change(
                        _snap_symphonies_list,
                        _snap_bot_state,
                        trading_day=_snap_trading_day,
                        conn=_frozen_shadow_conn,
                        include_paired_guard_delta=True,
                    )
                    _snap_vw_cr = analytics.get_portfolio_cumulative_return(
                        _snap_symphonies_list,
                        _snap_bot_state,
                        trading_day=_snap_trading_day,
                        conn=_frozen_shadow_conn,
                    )

                    # Wrap TC and CR through the account-basis helpers INDEPENDENTLY
                    # (mirrors live path ~1183-1212) — a missing sibling field must never
                    # null a warm field's if_held.
                    if _snap_account_tc is not None:
                        _snap_tc_final = analytics.get_portfolio_today_change_account_basis(
                            _snap_vw_tc,
                            _snap_account_tc,
                            _snap_cached_value or 0.0,
                            _snap_symphony_value_sum,
                        )
                    else:
                        # No TC basis at all (no cache, no last-good): fall back to VW
                        # (honesty signalled below via the Tier-2 basis marker), matching
                        # the CR branch + the plan's documented default. DE-HELD-BASIS-001
                        # F5(a): mirror the live Tier-2 floor's paired guard_delta_vw
                        # re-derivation (see _project_today_change_floor's docstring).
                        _snap_tc_final = _project_today_change_floor(_snap_vw_tc)

                    if _snap_account_cr is not None:
                        _snap_cr_final = analytics.get_portfolio_cumulative_return_account_basis(
                            _snap_vw_cr,
                            _snap_account_cr,
                            _snap_cached_value or 0.0,
                            _snap_symphony_value_sum,
                        )
                    else:
                        # No CR basis at all: surface raw VW (honesty signalled below via
                        # the Tier-2 basis marker, since if_held stays a real number here).
                        _snap_cr_final = _snap_vw_cr

                    _portfolio_strip = {
                        "today_change": _snap_tc_final,
                        "cumulative_return": _snap_cr_final,
                        "max_drawdown": analytics.get_portfolio_max_drawdown(
                            _snap_symphonies_list,
                            _snap_bot_state,
                            trading_day=_snap_trading_day,
                            conn=_frozen_shadow_conn,
                        ),
                        "account_value": (
                            _snap_cached_value
                            if _snap_cached_value is not None
                            else sum(
                                v.get("current_value") or 0.0
                                for v in _snap_bot_state.values()
                                if isinstance(v, dict)
                            )
                        ),
                        # Anchor to the snapshot's REAL capture time so the operator
                        # sees data age, not dashboard render time.
                        "data_as_of": _snap_data_as_of,
                    }
                    # Tier 2 honest-floor marker — fires when EITHER field has no basis
                    # at all (no cache, no last-good), signalling to the UI that at
                    # least one value on the strip is raw VW, not account basis.
                    if _snap_account_tc is None or _snap_account_cr is None:
                        _portfolio_strip["basis"] = "value_weighted"
                    # Tier 1 stale stamps — signals that last-good data (not current) was used.
                    if _snap_basis_stale:
                        _portfolio_strip["account_basis_stale"] = True
                        _portfolio_strip["account_basis_as_of"] = _snap_basis_as_of
                    # Mirror _compute_portfolio_strip: surface the Composer account-lifetime
                    # CR as the "Account · all-time" stat so the template's
                    # {% if _acct_cr is not none %} guard includes the element.
                    _acct_cr = _account_totals_cache.get("portfolio_cr")
                    if isinstance(_acct_cr, (int, float)):
                        _portfolio_strip["account_all_time_cr"] = _acct_cr

                    # DE-CLOSED-BOUNCE-001: mirror the open path's own independently-
                    # guarded default-window strip (app.py:1863-1881) so the closed
                    # branch's portfolio_strip carries guard_alpha/window too. A null
                    # guard_alpha here is what makes index.js's renderGuardAlpha fall
                    # back to fetchWindowedStrip on every closed-market poll — this
                    # block removes that trigger in the normal case. Deliberately its
                    # own try/except (not folded into the surrounding try) so a
                    # failure here can't discard the today_change/cumulative_return/
                    # max_drawdown already computed above. Does NOT set
                    # windowed_cumulative_return (unlike the open path) — that field
                    # has no client-side consumer since F-014 removed its only read
                    # site; adding a client-unread field here would just be dead
                    # weight on every closed-market poll response.
                    try:
                        _snap_default = analytics.compute_windowed_portfolio_strip(
                            _snap_symphonies_list,
                            _snap_bot_state,
                            window=_DEFAULT_HERO_WINDOW,
                            conn=_frozen_shadow_conn,
                        )
                        if isinstance(_snap_default, dict):
                            _snap_ga = _snap_default.get("guard_alpha")
                            _portfolio_strip["guard_alpha"] = (
                                _snap_ga if isinstance(_snap_ga, (int, float)) else None
                            )
                            _snap_win = _snap_default.get("window", _DEFAULT_HERO_WINDOW)
                            _portfolio_strip["window"] = (
                                _snap_win if isinstance(_snap_win, str) else _DEFAULT_HERO_WINDOW
                            )
                    except Exception:
                        _daemon_log.error(
                            "closed_frozen default-window strip failed", exc_info=True
                        )
                except Exception:
                    _portfolio_strip = {
                        "today_change": None,
                        "cumulative_return": None,
                        "max_drawdown": None,
                        "account_value": _account_totals_cache.get("portfolio_value"),
                        # Same frozen-snapshot semantics as the happy path above.
                        "data_as_of": _snap_data_as_of,
                    }

                # F-1: both the per-symphony loop and the portfolio-level calls above
                # are done with the shared connection by this point (happy path or
                # the except-fallback above — either way this line is reached).
                if _frozen_shadow_conn is not None:
                    _frozen_shadow_conn.close()

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
                _frozen_live_mode = _frozen_env.get("LIVE_EXECUTION", "False").lower() in (
                    "true",
                    "1",
                    "yes",
                )
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
            if isinstance(_injected_portfolio_strip, dict) and _injected_portfolio_strip.get(
                "hist_dates"
            ):
                _wait_strip = {
                    "hist_dates": _injected_portfolio_strip.get("hist_dates", []),
                    "hist_bot": _injected_portfolio_strip.get("hist_bot", []),
                    "hist_held": _injected_portfolio_strip.get("hist_held", []),
                }
            _waiting_body = {
                **waiting_resp,
                "bot_state": _api_state.get("bot_state", {}),
                "meta": _build_meta({}, market_state=market_state, portfolio_strip=_wait_strip),
                **_additive,
            }
            if _api_state_has_strip:
                _waiting_body["portfolio_strip"] = _injected_portfolio_strip
            return jsonify(_waiting_body)

        # FP-T3-03 backend: optional ?account=<uuid> filter on bot_state.
        _account_filter = request.args.get("account")
        if _account_filter:
            state_data = {
                k: v
                for k, v in state_data.items()
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
            k
            for k in state_data.keys()
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
            # DE-HELD-BASIS-001 F4 (PR #125 review): no current_return_is_reconstructed
            # key threaded above — analytics.get_symphony_today_change reads the marker
            # from bot_state_entry (the real `s`/state_data[k] dict passed at each call
            # site below, already carrying BL-9's marker) as the PRIMARY source, so this
            # per-symphony sym_dict threading is redundant by construction (removed, was
            # added in the original cycle before the F4 design pivot).

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
        # F-1: ONE shared read-only connection for the whole per-symphony
        # enrichment loop AND the _compute_portfolio_strip call right below it
        # (was: each of the 3 per-symphony analytics calls, PLUS every
        # portfolio-level CR/TC/MDD helper's own internal per-symphony loop,
        # opened its own connect() — ~157 connects/poll on a real portfolio).
        # Function-local/per-request scope only — opened here, closed in the
        # finally below, never a module-global. Falls back to None (each
        # analytics call opens its own connection, today's behavior) if the
        # shared connection itself fails to open.
        try:
            _shadow_conn = sqlite3.connect(
                f"file:{analytics._get_shadow_db_file()}?mode=ro", uri=True, timeout=10.0
            )
        except Exception:
            _shadow_conn = None
        try:
            for k in symphony_keys:
                s = state_data[k]
                sym_dict = next((d for d in symphonies_list if d["id"] == k), {})
                try:
                    s["_tc"] = analytics.get_symphony_today_change(
                        sym_dict, s, trading_day=_today_et, conn=_shadow_conn
                    )
                except (KeyError, TypeError, ValueError):
                    s["_tc"] = {"if_held": None, "dry_run": None}
                try:
                    s["_cr"] = analytics.get_symphony_cumulative_return(
                        sym_dict, s, trading_day=_today_et, conn=_shadow_conn
                    )
                except (KeyError, TypeError, ValueError):
                    s["_cr"] = {"if_held": None, "dry_run": None}
                try:
                    s["_mdd"] = analytics.get_symphony_max_drawdown(
                        sym_dict, s, trading_day=_today_et, conn=_shadow_conn
                    )
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
            portfolio_strip = _compute_portfolio_strip(
                state_data, trading_day=_today_et, conn=_shadow_conn
            )
        finally:
            if _shadow_conn is not None:
                _shadow_conn.close()

        # AC-7: top-level data_as_of is the JS fallback hero freshness signal
        # (index.js: `portfolio.data_as_of || data.data_as_of`).  Derive it from
        # last_successful_cycle_at in state_data — same pattern as
        # _compute_portfolio_strip (app.py:1281-1303) — so the operator sees real
        # data age, not the server render clock.  Falls back to datetime.now(_ET)
        # when no cycle timestamp is available.  Also fixes the pre-existing naive
        # datetime.now() (no timezone) bug — the original produced local-system time,
        # not ET.
        # The engine writes last_successful_cycle_at at the TOP LEVEL of state_data
        # (alpha_bot_execution.py:948/1092/1878) — read it directly.
        _tl_cycle_ts = state_data.get("last_successful_cycle_at")
        if _tl_cycle_ts:
            try:
                _tl_dt = datetime.fromisoformat(_tl_cycle_ts.replace("Z", "+00:00"))
                if _tl_dt.tzinfo is None:
                    _tl_dt = _tl_dt.replace(tzinfo=_ET)
                data_as_of = _tl_dt.astimezone(_ET).strftime("%H:%M ET")
            except Exception:
                data_as_of = datetime.now(_ET).strftime("%H:%M ET")
        else:
            data_as_of = datetime.now(_ET).strftime("%H:%M ET")

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
                k for k, v in state_data.items() if isinstance(v, dict) and v.get("triggered")
            ]
            if _triggered_ids:
                _ga_map = database.get_guard_alpha_by_symphony(_triggered_ids)
                for _sid, _ga in _ga_map.items():
                    if _sid in state_data and isinstance(state_data[_sid], dict):
                        _cr = state_data[_sid].get("current_return") or 0.0
                        state_data[_sid].setdefault(
                            "guard_alpha", round(float(_ga) - float(_cr), 6)
                        )
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
            # F-016 (3rd locus): the trailing `or None` this function used to have
            # converted a genuine 0.0 (falsy in Python) into a fabricated null --
            # `.get()` on a missing key already returns None, so `or None` was
            # redundant AND the only thing silently misrendering a real "no change
            # today" (or cumulative/MDD) value as the empty-state '--' on every
            # /api/state poll. Same pattern, same fix, on all six fields.
            tc_bot = tc.get("dry_run") if isinstance(tc, dict) else tc
            tc_held = tc.get("if_held") if isinstance(tc, dict) else None
            cr_bot = cr.get("dry_run") if isinstance(cr, dict) else cr
            cr_held = cr.get("if_held") if isinstance(cr, dict) else None
            mdd_bot = mdd.get("dry_run") if isinstance(mdd, dict) else mdd
            mdd_held = mdd.get("if_held") if isinstance(mdd, dict) else None
            return tc_bot, tc_held, cr_bot, cr_held, mdd_bot, mdd_held

        _symphonies_for_cards: list[dict] = []
        for _k in symphony_keys:
            _s = state_data[_k]
            _tc_b, _tc_h, _cr_b, _cr_h, _mdd_b, _mdd_h = _tc_cr_mdd_floats(_s)
            _symphonies_for_cards.append(
                {
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
                    "tc_bot": _tc_b,
                    "tc_held": _tc_h,
                    "cr_bot": _cr_b,
                    "cr_held": _cr_h,
                    "mdd_bot": _mdd_b,
                    "mdd_held": _mdd_h,
                }
            )

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


def _slice_series_by_window_cutoff(
    series: tuple[list, list, list] | None, window: object
) -> tuple[list, list, list] | None:
    """Slice a (dates, a, b) series to the CALENDAR cutoff analytics._window_cutoff_date
    resolves for `window` — the SAME cutoff function /api/strip's
    compute_windowed_portfolio_strip already canonicalizes (AC-5 / MAPERF-03), so a
    picker click covers the same calendar span on the hero chart, the strip, and
    /api/performance's "ytd" token alike.

    `window="all"` (and any unrecognized token) resolves to a None cutoff — no
    filtering, the full series passes through — matching _window_cutoff_date's own
    lifetime semantics.

    Degrades to "no filter" (rather than raising) when the cutoff resolution
    doesn't yield a real date — e.g. under a fully-mocked `analytics` module in
    older route tests, `_window_cutoff_date` returns a Mock, not a date/None; the
    conservative behavior is to pass the series through unfiltered, not to crash
    or silently empty it.
    """
    if series is None:
        return None
    dates, series_a, series_b = series
    try:
        cutoff = analytics._window_cutoff_date(window)
    except Exception:
        cutoff = None
    cutoff_iso = cutoff.isoformat() if isinstance(cutoff, date) else None
    idx = [i for i, d in enumerate(dates) if cutoff_iso is None or str(d) >= cutoff_iso]
    return [dates[i] for i in idx], [series_a[i] for i in idx], [series_b[i] for i in idx]


@app.route("/api/hero-chart/<window>")
def get_hero_chart(window):
    """Return hist_dates/hist_bot/hist_held for the requested time window.

    window values: 30d, 60d, 90d, 125d, ytd, 1y, all

    AC-5 (MAPERF-03): every window token resolves to the SAME calendar cutoff
    /api/strip already uses (analytics._window_cutoff_date) — the chart fetches
    the full shadow_history series once and slices it to that cutoff, instead of
    trading-day-slicing a per-token day count. Before this fix the SAME picker
    click windowed the chart by TRADING days and the strip by CALENDAR days
    (e.g. "30d" = last 30 trading days on the chart vs trading days within the
    last 30 calendar days on the strip — a ~40% window mismatch at "1y").
    """
    # Minimum trading days needed for the window to be meaningful (soft UI signal
    # only — not a math correctness gate). "all" has no floor. Scaled down from
    # the pre-AC-5 trading-day-count thresholds by ~252/365 now that windows are
    # calendar-based (fewer trading days fall inside the same calendar span).
    _min_days = {"30d": 14, "60d": 28, "90d": 42, "125d": 55, "ytd": 7, "1y": 70, "all": 2}
    required = _min_days.get(window, 7)

    def _compound(daily: list[float]) -> list[float]:
        """Compound a per-day pct return series into a running cumulative-return curve."""
        running = 1.0
        out = []
        for d in daily:
            running *= 1.0 + d / 100.0
            out.append(round((running - 1.0) * 100.0, 4))
        return out

    try:
        # AC-4b: use the REAL (bot, held) daily-return source so the dashed "If held"
        # line is a genuine second series, not a verbatim copy of Bot. bot = guarded
        # shadow path; held = un-guarded if-held path (diverges only after a trigger).
        bh = analytics.get_portfolio_bot_and_held_daily_returns(days=None)
        sliced = _slice_series_by_window_cutoff(bh, window)
        if sliced is not None:
            dates, bot_daily, held_daily = sliced
            # Each series is compounded INDEPENDENTLY into its own cumulative curve.
            bot_series = _compound(bot_daily)
            held_series = _compound(held_daily)
            insufficient = len(dates) < required
            return jsonify(
                {
                    "hist_dates": dates,
                    "hist_bot": bot_series,
                    "hist_held": held_series,
                    "window": window,
                    "source": "shadow_history",
                    "insufficient_history": insufficient,
                    "available_days": len(dates),
                }
            )
    except Exception:
        _daemon_log.error("get_hero_chart bot/held series failed", exc_info=True)

    return jsonify(
        {
            "hist_dates": [],
            "hist_bot": [],
            "hist_held": [],
            "window": window,
            "insufficient_history": True,
            "available_days": 0,
        }
    )


# Window tokens accepted by the picker + the windowed-strip route. Lowercase URL
# tokens are the canonical UI form; analytics.compute_windowed_portfolio_strip
# normalizes them internally (30d->30 days, all->lifetime cross-epoch, etc.).
_STRIP_WINDOW_TOKENS = {"30d", "60d", "90d", "125d", "ytd", "1y", "all"}


@app.route("/api/strip/<window>")
def get_windowed_strip(window):
    """Return the comparison strip (guard-alpha/CR/MDD/vol) recomputed FOR a window.

    AC-3: the time picker must re-window EVERY hero metric, not just the chart.
    This route threads the selected window token through to
    analytics.compute_windowed_portfolio_strip so the headline guard-alpha VALUE
    (and CR/MDD/vol) reflects the chosen window — 30d/60d/90d/125d/ytd/1y and the
    NEW "all" (All Time, lifetime cross-epoch) token. The returned dict echoes the
    resolved window so the UI label always matches the value (kills the "30d" label
    on an all-time number — F1).

    Read-only: builds the symphony list from the state DB; never reruns the engine.
    """
    token = (window or "").lower()
    if token not in _STRIP_WINDOW_TOKENS:
        return jsonify({"error": "unknown window token"}), 404

    trading_day = datetime.now(_ET).strftime("%Y-%m-%d")
    try:
        bot_state = database.load_state() or {}
    except Exception:
        bot_state = {}
    symphony_keys = [k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v]
    symphonies_list = []
    for k in symphony_keys:
        s = bot_state[k]
        symphonies_list.append(
            {
                "id": k,
                "value": s.get("current_value") or 0.0,
                "last_percent_change": (s.get("current_return") or 0.0) / 100.0,
                "simple_return": s.get("simple_return"),
                "net_deposits": s.get("net_deposits"),
                "time_weighted_return": s.get("time_weighted_return"),
                "max_drawdown": s.get("max_drawdown"),
                "trading_day": trading_day,
            }
        )

    try:
        strip = analytics.compute_windowed_portfolio_strip(symphonies_list, bot_state, window=token)
    except Exception:
        _daemon_log.error("get_windowed_strip failed for window=%s", token, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500

    # AC-4: when shadow_history has only 1 distinct trading_day the windowed
    # guard_alpha is None (zero weight) and insufficient_history=True.  Compute
    # an intraday estimate from exit_triggers so the day-1 droplet shows a
    # non-zero value.  The 15-second auto-refresh floor means this stays fresh.
    #
    # AC-4b: bot_state is a SINGLE-ROW JSON BLOB (id, data TEXT) — there is no
    # position_value column and no symphony_id column.  Mirror the AC-1b fix:
    # use database.load_state() (isolated try/except → degrades to {}).
    #
    # AC-8 (MAPERF-04): explicit `is None` check — `not strip.get("guard_alpha")`
    # was also True for a LEGITIMATE windowed 0.0 (an untriggered symphony yields
    # a genuine 0.0 divergence on every window; analytics.py's
    # compute_windowed_symphony_guard_alpha docstring), silently overwriting a
    # real zero with this cross-day estimate.
    if strip.get("insufficient_history") and strip.get("guard_alpha") is None:
        try:
            try:
                _bot_state_dict = database.load_state()
            except Exception:
                _bot_state_dict = {}
            _conn = database.get_connection()
            try:
                # AC-8 (MAPERF-04): day-filtered to the CURRENT ET trading day —
                # the query was previously unfiltered, pairing every exit_triggers
                # row EVER recorded (including stale, prior-day rows) against the
                # symphony's LATEST current_return, subtracting returns from two
                # different days' bases (cross-day incoherent). Mirrors the
                # /api/history intraday backfill's substr(ts_et,1,10) pattern.
                try:
                    _rows = _conn.execute(
                        "SELECT t.symphony_id, t.at_return, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t WHERE substr(t.ts_et, 1, 10) = ?",
                        (trading_day,),
                    ).fetchall()
                except Exception:
                    # A minimal/legacy exit_triggers table without a ts_et column
                    # cannot express "today" at all — degrade to the unfiltered
                    # query (the pre-AC-8 behavior) rather than silently zeroing
                    # out; a real (migrated) schema always carries ts_et, so this
                    # path is schema-compatibility only. Same pattern as the
                    # guard-alpha-summary fallback below.
                    _rows = _conn.execute(
                        "SELECT t.symphony_id, t.at_return, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t"
                    ).fetchall()

            finally:
                _conn.close()

            _alpha_wsum = 0.0
            _weight_sum = 0.0
            for _sym_id, _at_ret, _cur_ret in _rows:
                _pos_val = (_bot_state_dict.get(_sym_id) or {}).get("current_value")
                if _at_ret is None or _cur_ret is None or _pos_val is None:
                    continue
                try:
                    _w = float(_pos_val)
                    if _w <= 0.0:
                        continue
                    # Guard alpha = at_return − current_return (locked in vs held)
                    _alpha_wsum += (float(_at_ret) - float(_cur_ret)) * _w
                    _weight_sum += _w
                except (TypeError, ValueError):
                    continue

            if _weight_sum > 0.0:
                strip = dict(strip)
                strip["guard_alpha"] = _alpha_wsum / _weight_sum
                strip["intraday_only"] = True
        except Exception:
            _daemon_log.debug("strip intraday fallback failed", exc_info=True)

    return jsonify(strip)


def _is_symphony_state_entry(value) -> bool:
    """Structural discriminator: does this bot_state top-level value describe a
    real symphony, not portfolio-level metadata (date/last_execution_mode/
    last_market_close_snapshot/last_successful_cycle_at/post_mortem_run)?

    Mirrors the existing "isinstance(v, dict) and 'name' in v" shape check
    used elsewhere in this file (e.g. app.py:1205, 1214, 1320, 1363, 1504,
    2760, 3012) -- every real symphony entry is stamped with "name"
    unconditionally each cycle (alpha_bot_execution.py:1633) as soon as it is
    created, which no top-level metadata value (a plain str/bool, or a
    differently-shaped dict like last_market_close_snapshot) ever carries.
    Structural, not a name denylist (AC-2): a future metadata key is excluded
    automatically as long as it doesn't happen to be a dict carrying a "name"
    key.
    """
    return isinstance(value, dict) and "name" in value


@app.route("/api/guard-alpha-preconditions")
def guard_alpha_preconditions():
    """Per-symphony Kaminski & Lo (2014) stop-justification preconditions.

    Contract (feature-plans/guard-alpha-preconditions.md AC-6, AC-7, AC-8): for
    every symphony in database.load_state(), returns a "replay" sample
    (simulated if-held daily series from the last autotune/replay run) and a
    "shadow" sample (live if-held daily series since the current position was
    opened) -- {rho, rho_ci, sharpe_daily, n_obs, verdict, sample_source}.

    UNIFORM DEGRADED-ROW CONTRACT: both "replay" and "shadow" are ALWAYS a
    full 6-field object, never bare null. When real data is available, all 6
    fields come from a real run of compute_persistence_stats +
    classify_stop_justification. When unavailable (cold/missing replay cache,
    or a symphony with no shadow_history yet), the row degrades to
    {rho/rho_ci/sharpe_daily: None, n_obs: 0, verdict: "INSUFFICIENT_DATA"} --
    the same 5-class vocabulary a genuinely-thin real sample would produce,
    so callers never have to null-check a sample before reading its fields.
    sample_source stays STABLE per sample family ("if_held_replay" /
    "shadow_history") whether the row is real or degraded -- it identifies
    WHICH sample this is, never WHY it's unavailable (AC-8's disagreement
    display keys on sample identity).

    Cache-hit-only on the replay sample (operator ruling): this route never
    triggers a network fetch or 250-day history assembly to backfill a cold
    cache -- autotuner.build_if_held_replay_series may serve from its local
    file-cache CPU-only, but a cache miss degrades per the contract above
    rather than fetching.

    Read-only -- no SQL in this route; database.load_state, the two sample
    accessors, and the math layer are the only calls made. Auth via the
    global _auth_before_request hook (no extra decorator needed, same as
    guard_alpha_summary). Never a 500 -- one symphony's data-source failure
    degrades just that entry (AC-6).
    """
    import autotuner  # noqa: PLC0415 -- lazy per CC-2 precedent (app.py:3430), keeps Optuna deps off module load
    import guard_preconditions as gp

    try:
        bot_state_dict = database.load_state()
    except Exception:
        _daemon_log.debug("guard_alpha_preconditions: load_state failed", exc_info=True)
        bot_state_dict = {}

    def _json_safe_float(value):
        # Strict-JSON guard (RFC 8259): json.dumps serializes float('nan') /
        # float('inf') as the bare tokens NaN/Infinity, which a real
        # browser's response.json() rejects -- invalidating the WHOLE
        # response, not just this field. compute_persistence_stats
        # legitimately returns nan on a genuinely flat (zero-variance)
        # series -- that's correct math-layer behavior; sanitize only at
        # this JSON-facing boundary, never in the stats object itself (the
        # verdict classification below runs against the real, unsanitized
        # stats).
        if value is None or math.isnan(value) or math.isinf(value):
            return None
        return value

    def _sample_row(daily_returns, sample_source):
        stats = gp.compute_persistence_stats(daily_returns)
        return {
            "rho": _json_safe_float(stats.rho),
            "rho_ci": _json_safe_float(stats.rho_ci),
            "sharpe_daily": _json_safe_float(stats.sharpe_daily),
            "n_obs": stats.n_obs,
            "verdict": gp.classify_stop_justification(stats),
            "sample_source": sample_source,
        }

    def _degraded_row(sample_source):
        # Hand-built rather than routed through compute_persistence_stats([])
        # -- avoids depending on unpinned empty-list behavior in the math
        # layer; sample_source stays stable per the uniform contract above.
        return {
            "rho": None,
            "rho_ci": None,
            "sharpe_daily": None,
            "n_obs": 0,
            "verdict": "INSUFFICIENT_DATA",
            "sample_source": sample_source,
        }

    db_file = analytics._get_shadow_db_file()
    symphonies_out: dict = {}

    for sym_id, sym_data in bot_state_dict.items():
        if not _is_symphony_state_entry(sym_data):
            continue
        try:
            try:
                replay_series = autotuner.build_if_held_replay_series(sym_id)
            except Exception:
                _daemon_log.debug(
                    "guard_alpha_preconditions: replay series failed for %s",
                    sym_id,
                    exc_info=True,
                )
                replay_series = None

            replay_row = (
                _sample_row(replay_series, "if_held_replay")
                if replay_series
                else _degraded_row("if_held_replay")
            )

            try:
                shadow_series = analytics.get_shadow_current_return_daily_series(sym_id, db_file)
            except Exception:
                _daemon_log.debug(
                    "guard_alpha_preconditions: shadow series failed for %s",
                    sym_id,
                    exc_info=True,
                )
                shadow_series = None

            shadow_row = (
                _sample_row(shadow_series, "shadow_history")
                if shadow_series
                else _degraded_row("shadow_history")
            )

            symphonies_out[sym_id] = {"replay": replay_row, "shadow": shadow_row}
        except Exception:
            # AC-6: one symphony's failure must never 500 the whole route --
            # skip this entry entirely rather than surface a half-built one.
            _daemon_log.debug(
                "guard_alpha_preconditions: symphony %s degraded entirely",
                sym_id,
                exc_info=True,
            )

    return jsonify({"symphonies": symphonies_out})


@app.route("/api/guard-alpha-summary")
def guard_alpha_summary():
    """Return cumulative dollar-saved + guard-event count from post_mortem JSON files.

    AC-1: aggregates Σ saved_dollars and trigger count across all post_mortem_*.json
    files in analytics._POST_MORTEMS_DIR (the same directory constant used by every
    other route that reads post_mortems). Read-only — no DB writes, not in
    _SETTINGS_WRITE_ALLOWLIST, no LIVE_EXECUTION interaction (AC-4). Malformed or
    unreadable files are skipped and logged without surfacing file contents (AC-6).
    Honest empty-state when no files exist (AC-5).

    Auth: covered by the global _auth_before_request before_request hook (AC-8) —
    no additional decorator needed; unauthenticated XHR requests receive 401.

    Returns JSON:
        cumulative_saved_dollars (float): Sum of saved_dollars across all trigger
            entries in all post_mortem_*.json files. 0.0 when no files exist.
        guard_event_count (int): Total trigger count across all files. 0 when none.
        date_range (dict): {"earliest": str|None, "latest": str|None} — YYYY-MM-DD
            strings extracted from filenames; both None when no files exist.
        basis_label (str): "snapshot-time basis, since <earliest>" when files exist;
            "no guard events yet" for empty-state.
        saved_dollars_realized (float): AC-6 (exit-friction-realized-savings) —
            sum of saved_dollars_realized across valid trigger entries that carry
            the field. Additive to cumulative_saved_dollars, never a replacement;
            0.0 when no entries have realized data. This is an EOD-marks basis
            (RULING A) — it captures post-snapshot drift through the rebalance
            window, not fill-level slippage.
        realized_coverage (dict): {"with_data": int, "total": int} — AC-7: an
            entry missing realized data is EXCLUDED from saved_dollars_realized,
            COUNTED in total, never counted in with_data, never substituted with
            the snapshot-basis value.
        window (str): AC-2/AC-3 — the resolved window token (mirrors
            /api/strip/<window>'s own echo-back pattern). Omitted or unrecognized
            `?window=` query values resolve to "all" (lifetime, today's original
            behavior) — never a 404/500, this is a read-only advisory route.
        cumulative_saved_dollars_net_of_friction (float): BL-3 (friction-aware
            $-saved headline, DE-AUDIT-BL3-001) — snapshot-basis sibling of
            cumulative_saved_dollars, net of autotuner.SIM_EXIT_FRICTION_PCT
            (the same friction floor the optimizer's own replay accounting
            already assumes). Friction subtracted at the PERCENTAGE level per
            entry before dollar conversion (reporting.py:92-95's own
            saved_dollars formula shape). An entry missing symphony_value or
            saved_pct_guard_alpha is excluded from this sum (presence-guarded,
            never a fabricated 0.0). Respects the same `?window=` filtering as
            the gross field. 0.0 in the empty state.
        saved_dollars_realized_net_of_friction (float): realized-basis sibling
            of saved_dollars_realized, same friction-net contract — only
            entries carrying BOTH saved_dollars_realized and symphony_value
            contribute; never substitutes the snapshot-basis value for a
            missing realized entry.

    AC-2 (DE-GAS-COHERENCE-001): an optional `?window=<token>` query param, using
    the SAME token vocabulary as /api/strip/<window> (_STRIP_WINDOW_TOKENS) and
    resolved via the SAME analytics._window_cutoff_date the strip route uses —
    filters both the summed cumulative_saved_dollars and date_range to only the
    in-window post_mortem files. Omitting the param preserves the pre-existing
    all-time default (regression guard for existing callers of this route).
    """
    import glob as _glob
    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    import autotuner  # noqa: PLC0415 — lazy per CC-2 precedent (app.py:3625), sole owner of SIM_EXIT_FRICTION_PCT

    raw_window = (request.args.get("window") or "").lower()
    resolved_window = raw_window if raw_window in _STRIP_WINDOW_TOKENS else "all"
    _cutoff = analytics._window_cutoff_date(resolved_window)
    _cutoff_iso = _cutoff.isoformat() if _cutoff is not None else None

    pm_dir = analytics._POST_MORTEMS_DIR
    pattern = os.path.join(pm_dir, "post_mortem_*.json")
    files = sorted(_glob.glob(pattern))

    cumulative_saved_dollars = 0.0
    guard_event_count = 0
    excluded_invalid_count = 0
    dates: list[str] = []
    # AC-6/AC-7 (exit-friction-realized-savings): realized-basis aggregate +
    # coverage counters, additive to the snapshot-basis aggregate above.
    # Initialized here — before both the post-mortem-files loop below AND the
    # day-1 exit_triggers-intraday fallback branch further down — so either
    # path returns the honest zeroed state when no realized data exists yet.
    saved_dollars_realized = 0.0
    realized_with_data = 0
    realized_total = 0
    # BL-3 (friction-aware $-saved headline, DE-AUDIT-BL3-001): net-of-friction
    # siblings to cumulative_saved_dollars/saved_dollars_realized above — same
    # additive-only, zero-init-on-empty-state contract. Friction is subtracted
    # at the PERCENTAGE level before dollar conversion (reporting.py:92-95's
    # own saved_dollars formula shape), never a post-hoc subtraction on the
    # aggregate dollar sum.
    cumulative_saved_dollars_net_of_friction = 0.0
    saved_dollars_realized_net_of_friction = 0.0

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as fh:
                pm = _json.load(fh)
        except (OSError, _json.JSONDecodeError):
            _daemon_log.warning(
                "guard_alpha_summary: skipping unreadable file %s", os.path.basename(fpath)
            )
            continue

        # Extract YYYY-MM-DD from filename post_mortem_YYYY-MM-DD.json — done
        # BEFORE the trigger-aggregation loop below so AC-2's window cutoff can
        # skip an out-of-window file entirely (date_range/AC-3 must reflect only
        # the in-window file dates, not the all-time range).
        basename = os.path.basename(fpath)
        date_str = basename[len("post_mortem_") : len("post_mortem_") + 10]

        if _cutoff_iso is not None and (len(date_str) != 10 or date_str < _cutoff_iso):
            continue

        # F-008: only entries with a recognized if_held_source provenance stamp
        # contribute — a missing/unrecognized stamp means the if-held basis is
        # untrustworthy (see analytics.is_valid_post_mortem_entry). Distinct
        # from the malformed-file except-path above: this is a per-entry
        # semantic check, never a whole-file skip.
        triggers = pm.get("triggers", [])
        for t in triggers:
            if analytics.is_valid_post_mortem_entry(t):
                cumulative_saved_dollars += float(t.get("saved_dollars", 0.0))
                guard_event_count += 1
                # AC-6/AC-7/AC-9: saved_dollars_realized is additive-only — absent
                # on old-format files and on entries Stage-2 couldn't resolve a
                # shadow_history row for. Counted in realized_total regardless
                # (same denominator as guard_event_count); only counted in
                # realized_with_data and summed when the field is present —
                # never substituted with the snapshot-basis value.
                realized_total += 1
                if "saved_dollars_realized" in t:
                    saved_dollars_realized += float(t["saved_dollars_realized"])
                    realized_with_data += 1

                # BL-3: net-of-friction accumulation — presence-guarded (both
                # fields must be present AND numeric) so a legacy entry that
                # predates symphony_value/saved_pct_guard_alpha is EXCLUDED
                # from the net sum rather than KeyError-ing the whole route or
                # being coerced to a fabricated 0.0.
                _sv = t.get("symphony_value")
                _spg = t.get("saved_pct_guard_alpha")
                if isinstance(_sv, (int, float)) and isinstance(_spg, (int, float)):
                    cumulative_saved_dollars_net_of_friction += (
                        _sv * (_spg - autotuner.SIM_EXIT_FRICTION_PCT) / 100.0
                    )
                if "saved_dollars_realized" in t and isinstance(_sv, (int, float)):
                    saved_dollars_realized_net_of_friction += (
                        float(t["saved_dollars_realized"])
                        - _sv * autotuner.SIM_EXIT_FRICTION_PCT / 100.0
                    )
            else:
                excluded_invalid_count += 1

        if len(date_str) == 10:
            dates.append(date_str)

    if dates:
        earliest = min(dates)
        latest = max(dates)
        date_range = {"earliest": earliest, "latest": latest}
        # Finding 8: name the LATEST covered date so a number that excludes today
        # cannot read as current ("the number doesn't move while triggers fire").
        basis_label = f"snapshot-time basis, since {earliest} · through {latest}"
        source = "post_mortem_eod"
    elif files:
        # AC-2/AC-3 windowed-empty case (gas-review sufficiency finding): real
        # post-mortem files exist overall, just none fall inside the selected
        # window (every file hit the cutoff `continue` above) — an honest
        # window-scoped zero. Must NOT fall through to the day-1 exit_triggers
        # fallback below, which is reserved for "no post-mortem history exists
        # at all" and would dishonestly claim "no guard events yet" when a real
        # guard event exists, just outside this window.
        date_range = {"earliest": None, "latest": None}
        basis_label = "no guard events in this window"
        source = "post_mortem_eod"
    else:
        date_range = {"earliest": None, "latest": None}
        # No post-mortem files yet (day-1 droplet) — fall back to exit_triggers DB rows.
        # Intraday estimate: saved = (at_return - current_return) / 100 * position_value
        # Label clearly as an intraday estimate so the UI can qualify the display.
        #
        # AC-1b: bot_state is a SINGLE-ROW JSON BLOB (id, data TEXT) — there is no
        # position_value column and no symphony_id column.  Use database.load_state()
        # which parses the blob and returns a dict keyed by symphony_id.
        # Degrade to empty dict if the schema differs (no raise — the count query still runs).
        try:
            bot_state_dict = database.load_state()
        except Exception:
            bot_state_dict = {}
        try:
            conn = database.get_connection()
            try:
                count = conn.execute("SELECT COUNT(*) FROM exit_triggers").fetchone()[0]
                # AC-8 (MAPERF-04, same-class sibling of the strip fallback above):
                # the dollar-estimate rows are day-filtered to the CURRENT ET trading
                # day — pairing a stale (non-today) exit_triggers row against the
                # symphony's LATEST current_return is cross-day incoherent (returns
                # from two different days' bases). guard_event_count above stays the
                # true all-time COUNT(*) — only the money-math rows are day-scoped.
                _today_et = datetime.now(_ET).date().isoformat()
                try:
                    rows = conn.execute(
                        "SELECT t.symphony_id, t.at_return, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t WHERE substr(t.ts_et, 1, 10) = ?",
                        (_today_et,),
                    ).fetchall()
                except Exception:
                    # A minimal/legacy exit_triggers table without a ts_et column
                    # cannot express "today" at all — degrade to the unfiltered
                    # query (the pre-AC-8 behavior) rather than silently zeroing
                    # out; a real (migrated) schema always carries ts_et, so this
                    # path is schema-compatibility only, not a reintroduction of
                    # the cross-day estimate for a schema that CAN day-filter.
                    rows = conn.execute(
                        "SELECT t.symphony_id, t.at_return, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t"
                    ).fetchall()

            finally:
                conn.close()

            for _sym_id, _at_ret, _cur_ret in rows:
                _pos_val = (bot_state_dict.get(_sym_id) or {}).get("current_value")
                if _at_ret is None or _cur_ret is None or _pos_val is None:
                    continue
                try:
                    cumulative_saved_dollars += (
                        (float(_at_ret) - float(_cur_ret)) / 100.0 * float(_pos_val)
                    )
                    # AC-3: day-1 intraday fallback also nets friction — same
                    # shape as the gross estimate above, friction subtracted
                    # at the percentage level before the dollar conversion.
                    cumulative_saved_dollars_net_of_friction += (
                        ((float(_at_ret) - float(_cur_ret)) - autotuner.SIM_EXIT_FRICTION_PCT)
                        / 100.0
                        * float(_pos_val)
                    )
                except (TypeError, ValueError):
                    pass

            guard_event_count = count
            source = "exit_triggers_intraday"
            basis_label = "intraday estimate — updates live" if count > 0 else "no guard events yet"
        except Exception:
            _daemon_log.debug("guard_alpha_summary: exit_triggers fallback failed", exc_info=True)
            source = "exit_triggers_intraday"
            basis_label = "no guard events yet"

    return jsonify(
        {
            "cumulative_saved_dollars": cumulative_saved_dollars,
            "guard_event_count": guard_event_count,
            "excluded_invalid_count": excluded_invalid_count,
            "date_range": date_range,
            "basis_label": basis_label,
            "source": source,
            # AC-6 (exit-friction-realized-savings): realized (EOD-marks) basis,
            # additive to the snapshot-basis fields above — never a replacement
            # (DE-GUARD-ALPHA-SAVED-001 semantics preserved).
            "saved_dollars_realized": saved_dollars_realized,
            "realized_coverage": {"with_data": realized_with_data, "total": realized_total},
            "window": resolved_window,
            # BL-3 (friction-aware $-saved headline, DE-AUDIT-BL3-001): net-of-
            # friction siblings to the gross fields above, additive-only — see
            # docstring formula notes.
            "cumulative_saved_dollars_net_of_friction": cumulative_saved_dollars_net_of_friction,
            "saved_dollars_realized_net_of_friction": saved_dollars_realized_net_of_friction,
        }
    )


def _incubation_badge(row: dict) -> dict:
    """Compute a human-readable badge label + BEM modifier for one strategy_incubation
    ledger row (AC-5). Pure, no I/O -- shared by GET /api/incubation and the Strategy
    Builder persisted-survivor chip stamping in ai_advisor_tab() so wording never
    drifts between the two render sites. The caller is responsible for sourcing `row`
    LIVE (database.get_incubation_overview()) on every call -- never from a frozen
    advisor_observations field (AC-5 amendment: those rows are append-only/immutable).

    INCUBATION_WINDOW_TRADING_DAYS is lazily imported from advisors.incubation (CC-2)
    inside a try/except: if that module isn't present yet the "of N" suffix is simply
    omitted from the INCUBATING label -- an honest degrade, never a duplicated
    magic-number fallback and never a raise.
    """
    status = (row.get("status") or "").upper()
    reason = row.get("status_reason")
    days = row.get("days_observed")
    # days_observed is a SQL COUNT(*) -- always a real int in production. A
    # non-finite value (None, or a NaN injected upstream) degrades to 0 for
    # display rather than leaking a non-numeric token into the label text.
    _days_is_nonfinite = days is None or (isinstance(days, float) and math.isnan(days))
    days_display = 0 if _days_is_nonfinite else days

    if status == "PROMOTED":
        modifier = "promoted"
        label = "Promoted — recommended"
    elif status == "FAILED":
        modifier = "failed"
        label = f"Failed incubation ({reason})" if reason else "Failed incubation"
    elif status == "EXPIRED":
        modifier = "expired"
        label = f"Expired ({reason})" if reason else "Expired"
    else:
        # INCUBATING (or an unrecognized future status -- degrades to the same
        # "incubating"-shaped display rather than fabricating a 5th modifier).
        modifier = "incubating"
        try:
            from advisors.incubation import (
                INCUBATION_WINDOW_TRADING_DAYS,  # noqa: PLC0415 -- CC-2 lazy
            )

            label = f"Incubating — day {days_display} of {INCUBATION_WINDOW_TRADING_DAYS}"
        except Exception:
            label = f"Incubating — day {days_display}"

    return {"label": label, "modifier": modifier}


@app.route("/api/incubation")
def api_incubation():
    """Return the forward-incubation ledger for the Strategy Builder tab (AC-7).

    Read-only -- calls database.get_incubation_overview() fresh on every request,
    no caching anywhere in this path (two requests straddling a real status
    transition must return different values -- see
    tests/app/test_incubation_route.py::TestIncubationRouteNoStaleCaching).
    Covered by the global _auth_before_request hook (no extra decorator needed,
    same as guard_alpha_summary) -- NOT in _SETTINGS_WRITE_ALLOWLIST, no
    LIVE_EXECUTION interaction.

    Response shape: {"incubating": [{candidate_hash, status, status_reason,
    days_observed, admitted_at, promoted_at, objective, provenance, badge_label,
    badge_modifier}, ...]}. tree_json is stored server-side only and is NEVER
    read into this projection -- no tree exfil via the API (Strategy Builder
    already has its own tree display path with its own controls).

    Empty ledger -> {"incubating": []}, never a 500. A ledger read failure
    degrades to the same empty-list shape (this route IS the one section it
    covers) rather than 500ing.

    Strict-JSON safe: days_observed is sanitized through a local NaN/Infinity
    guard (RFC 8259 -- json.dumps would otherwise emit the bare tokens, which a
    real browser's response.json() rejects for the WHOLE response) before it
    ever reaches jsonify(), mirroring the shipped guard_alpha_preconditions
    pattern (this file's other _json_safe_float, app.py:3128).
    """

    def _json_safe_float(value):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value

    try:
        rows = database.get_incubation_overview()
    except Exception:
        _daemon_log.debug("api_incubation: ledger read failed", exc_info=True)
        rows = []

    out = []
    for row in rows:
        try:
            badge = _incubation_badge(row)
            out.append(
                {
                    "candidate_hash": row.get("candidate_hash"),
                    "status": row.get("status"),
                    "status_reason": row.get("status_reason"),
                    "days_observed": _json_safe_float(row.get("days_observed")),
                    "admitted_at": row.get("admitted_at"),
                    "promoted_at": row.get("promoted_at"),
                    "objective": row.get("objective"),
                    "provenance": row.get("provenance"),
                    "badge_label": badge["label"],
                    "badge_modifier": badge["modifier"],
                }
            )
        except Exception:
            # One malformed row must never break the whole route (AC-6).
            _daemon_log.debug("api_incubation: row degraded", exc_info=True)
            continue

    return jsonify({"incubating": out})


def _join_retirement_approval_status(recs: list[dict]) -> None:
    """Stamp approval_status onto each retirement recommendation dict in
    place, read FRESH from the mutable retirement_decisions table on every
    call -- never cached (Cycle 2c, AC-6).

    Shared by BOTH _fetch_retirement_recommendations (backs GET /api/
    retirement-recommendations) and the AI Advisor Overview-tab panel
    prefetch, so a programmatic API consumer and the rendered panel always
    agree on decision state. A database.get_retirement_decisions() read
    failure degrades every row to the honest "pending" default rather than
    raising -- mirrors this route's existing per-row-degrades-gracefully
    convention (see api_incubation above). Never raises.

    PR#140 2nd /code-review finding 6 (Cycle 2d, AC-5): a no-op on an empty
    list, BEFORE calling database.get_retirement_decisions() -- mirrors
    _refresh_retirement_display_names's own existing empty-list skip. Avoids
    an unnecessary DB read on the honest-empty-state common case (no
    RETIREMENT_RECOMMENDATION rows, or every row in the batch malformed).
    """
    if not recs:
        return
    try:
        decisions_by_id = {
            row.get("candidate_id"): row for row in database.get_retirement_decisions()
        }
    except Exception:
        decisions_by_id = {}
    for rec in recs:
        decision = decisions_by_id.get(rec.get("candidate_id"))
        rec["approval_status"] = (decision or {}).get("approval_status") or "pending"


def _refresh_retirement_display_names(
    recs: list[dict], bot_state_getter: Callable[[], dict] | None = None
) -> None:
    """Overwrite candidate_name/sibling_name on each rec with a FRESH
    resolution against CURRENT bot_state, in place.

    PR#140 /code-review finding 3 (Cycle 2c): AC-2's tick-time enrichment
    persists candidate_name/sibling_name into the raw_response (needed so
    AC-4's reuse gate, above, can detect a rename against a prior night --
    see _canonical_evidence_snapshot). But GET /api/retirement-recommendations
    returned that raw_response verbatim, while ai_advisor_tab()'s panel
    resolved the display name FRESH at request time -- if a symphony was
    renamed between the 03:45 tick and a later request, the API showed the
    STALE tick-time name while the panel showed the CURRENT name for the
    SAME candidate_id, contradicting AC-6's "the API and panel never
    disagree" principle. Called from _fetch_retirement_recommendations, so
    BOTH the API route and the panel prefetch get the fix from one shared
    call site. This overwrite is entirely in-memory -- it never touches the
    persisted advisor_observations row, and AC-4's reuse gate reads that raw
    DB row directly via database.get_advisor_observations_for_role (a
    separate code path this never touches), so the tick-time value rename-
    detection needs stays intact in the database.

    PR#140 2nd /code-review finding 1 (Cycle 2d, AC-1): resolution is a
    3-tier fallback chain per field, independently for candidate/sibling --
    (1) freshly-resolved name against CURRENT bot_state (resolve_incumbent_
    display_name's own contract: returns the raw id verbatim when
    unresolvable, never None); (2) when tier 1 fell back to the raw id (i.e.
    genuinely unresolved against current bot_state), prefer the PERSISTED
    tick-time name already on the rec (rec.get("candidate_name"), stamped by
    AC-2's Cycle-2c tick-time enrichment) when truthy -- a symphony that has
    since left bot_state (renamed/removed/retired) must not regress to the
    hash while a known-good name is still available; (3) the raw id itself,
    last resort, when neither tier resolves anything (the original "never
    had a name" case). Tier 1 always wins over a stale persisted name when
    the symphony IS currently resolvable.

    AC-4 (Cycle 2d, finding 5): bot_state_getter is an optional zero-arg
    callable returning bot_state, threaded from _fetch_retirement_
    recommendations. None (the default -- used by api_retirement_
    recommendations()'s route-level call, which has no shared closure to
    reuse) preserves the ORIGINAL behavior of calling database.load_state()
    directly here. ai_advisor_tab()'s call passes its own memoized
    _ensure_ai_advisor_bot_state closure so this function's bot_state need
    is absorbed into that SAME cached load, instead of independently calling
    database.load_state() a second time on the same request.

    A no-op on an empty list (skips the bot_state load entirely -- no wasted
    I/O when there is nothing to resolve). A bot_state load failure degrades
    every name to its raw-id fallback, never raises.
    """
    if not recs:
        return
    try:
        bot_state = bot_state_getter() if bot_state_getter is not None else database.load_state()
    except Exception:
        bot_state = {}
    try:
        from advisors.frontrunner_builder import (  # noqa: PLC0415
            resolve_incumbent_display_name as _resolve_name,
        )
    except Exception:
        _resolve_name = None

    def _resolved_or_persisted_or_hash(raw_id, persisted_name):
        fresh = _resolve_name(bot_state, raw_id) if _resolve_name is not None else raw_id
        if fresh != raw_id:
            return fresh
        if persisted_name:
            return persisted_name
        return fresh

    for rec in recs:
        rec["candidate_name"] = _resolved_or_persisted_or_hash(
            rec.get("candidate_id"), rec.get("candidate_name")
        )
        rec["sibling_name"] = _resolved_or_persisted_or_hash(
            rec.get("sibling_id"), rec.get("sibling_name")
        )


def _fetch_retirement_recommendations(
    limit: int | None = None, bot_state_getter: Callable[[], dict] | None = None
) -> list[dict]:
    """Return the LATEST NIGHT's persisted RETIREMENT_RECOMMENDATION raw_response dicts.

    Shared by GET /api/retirement-recommendations (AC-9) and the AI Advisor
    Overview-tab panel prefetch (AC-10) -- one fetch+flatten implementation,
    never duplicated. Each returned dict IS the persisted raw_response verbatim
    (the authoritative schema -- candidate_id/sibling_id/correlation/etc, see
    .claude/tdd-handoff.md) -- no renaming/translation layer between
    advisors.retirement_recommender's persistence and this read path. Never
    recomputes/reruns the module -- read-only over already-persisted rows.
    ADDITIVE exceptions: each returned dict also carries a freshly-live-joined
    "approval_status" key via _join_retirement_approval_status (Cycle 2c,
    AC-6) and FRESH (not tick-time-stale) "candidate_name"/"sibling_name"
    keys via _refresh_retirement_display_names (PR#140 review finding 3) --
    neither is part of the persisted raw_response itself; both are computed
    fresh every call.

    bot_state_getter (Cycle 2d, AC-4, PR#140 2nd /code-review finding 5) is
    threaded straight through to _refresh_retirement_display_names -- None
    (the default, used by api_retirement_recommendations()'s route-level
    call) preserves the original per-call database.load_state() behavior;
    ai_advisor_tab()'s panel prefetch passes its own memoized
    _ensure_ai_advisor_bot_state closure so the name-refresh consumer shares
    ONE bot_state load with the checklist/frontrunner-identity consumers on
    the same request, instead of loading it again independently.

    limit defaults to _ADVISOR_OBSERVATIONS_PAGE_LIMIT, resolved lazily inside
    the function body (not as a parameter default) because that module-level
    constant is defined later in this file -- a def-time default would raise
    NameError at import.

    PR-level /code-review Finding 4: advisor_observations is append-only and
    the 03:45 daily tick (_run_retirement_recommender_tick) persists every
    night, so a multi-night deployment accumulates one row per night per
    still-flagged pair, plus stale rows for pairs no longer flagged. This
    filters to ONLY the rows sharing the MOST RECENT fetched row's calendar
    date (UTC, `created_at[:10]`) -- the same substr(created_at,1,10)-equals-
    MAX(...) "one batch, not the whole table" trick database.get_
    candidate_alert_last_run already established, applied Python-side here
    since get_advisor_observations_for_role already returns created_at on
    every row (no second query needed). A pair still flagged on consecutive
    nights produces a genuine append-only duplicate row -- the date filter
    naturally keeps only the latest night's row for that candidate too.

    Raises on a DB-read failure (does not swallow) -- each caller degrades to
    an empty result on its own terms (route: honest {"recommendations": []};
    panel: honest empty-state), matching this file's existing per-route error
    ownership convention (see api_incubation above).
    """
    if limit is None:
        limit = _ADVISOR_OBSERVATIONS_PAGE_LIMIT
    rows = database.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=limit)
    dated_rows = [row for row in rows if row.get("created_at")]
    if not dated_rows:
        return []
    latest_date = max(row["created_at"][:10] for row in dated_rows)
    out: list[dict] = []
    for row in dated_rows:
        if row["created_at"][:10] != latest_date:
            continue
        raw = row.get("raw_response")
        if isinstance(raw, dict):
            out.append(raw)
    _join_retirement_approval_status(out)
    _refresh_retirement_display_names(out, bot_state_getter=bot_state_getter)
    return out


@app.route("/api/retirement-recommendations")
def api_retirement_recommendations():
    """Return the current advisory retirement recommendations (AC-9).

    Read-only -- reads persisted RETIREMENT_RECOMMENDATION advisor_observations
    rows via _fetch_retirement_recommendations() (never recomputes/reruns
    advisors.retirement_recommender from this route). Covered by the global
    _auth_before_request hook (no extra decorator needed, same as
    guard_alpha_summary/api_incubation) -- NOT in _SETTINGS_WRITE_ALLOWLIST, no
    LIVE_EXECUTION interaction. GET never writes.

    Response shape: {"recommendations": [<raw_response dict, flattened to top
    level>, ...]}. Each item carries the authoritative raw_response schema
    verbatim (candidate_id, sibling_id, correlation, ci_lower, ci_upper,
    n_obs, candidate_composite, sibling_composite, candidate_metrics,
    sibling_metrics, uncertainty_gate_passed, structural_redundancy_gate_passed,
    stressed_correlation, holdings_overlap, basis_label), PLUS three
    additional keys -- approval_status, candidate_name, sibling_name (PR#140
    2nd /code-review finding 7) -- that are NOT part of the persisted
    raw_response schema above. These three are freshly-computed request-time
    overlays, live-joined/live-resolved on every single call via
    _fetch_retirement_recommendations() (approval_status via _join_
    retirement_approval_status against the mutable retirement_decisions
    table; candidate_name/sibling_name via _refresh_retirement_display_names
    against current bot_state) -- never cached, never stored back into the
    row's raw_response.

    Empty -> {"recommendations": []}, never a 500; a read failure degrades to
    the same empty list rather than 500ing (mirrors api_incubation).

    Strict-JSON safe: every numeric field (including nested candidate_metrics/
    sibling_metrics) is sanitized through a local NaN/Infinity guard (mirrors
    api_incubation's own _json_safe_float) before jsonify() -- a raw NaN/
    Infinity token would corrupt the WHOLE response for a real browser's
    response.json().
    """

    def _json_safe(value):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        return value

    try:
        raw_rows = _fetch_retirement_recommendations()
    except Exception:
        _daemon_log.debug("api_retirement_recommendations: read failed", exc_info=True)
        raw_rows = []

    out = []
    for raw in raw_rows:
        try:
            out.append({k: _json_safe(v) for k, v in raw.items()})
        except Exception:
            # One malformed row must never break the whole route.
            _daemon_log.debug("api_retirement_recommendations: row degraded", exc_info=True)
            continue

    return jsonify({"recommendations": out})


@app.route("/api/exit-turnover")
def exit_turnover():
    """Return per-symphony exit-trigger turnover stats + an estimated annual
    friction drag (AC-8, exit-friction-realized-savings).

    ?symphony_id=<id>  — required. Missing/empty -> 400 with a JSON error body
                         (never a 500, never a silent empty 200).

    Delegates to database.get_exit_turnover_stats (30/90/365-day exit_count +
    coverage_days per window — coverage_days is capped by retained history, so
    a retention-pruned 365-day window never silently claims a full year — see
    RULING C) and database.compute_est_annual_friction_drag_pct (pure
    arithmetic; takes autotuner.SIM_EXIT_FRICTION_PCT as an explicit parameter,
    no autotuner<->database import coupling).

    Read-only, covered by the global _auth_before_request hook (no extra
    decorator needed, same as guard_alpha_summary) — NOT in
    _SETTINGS_WRITE_ALLOWLIST, no LIVE_EXECUTION interaction.

    Returns JSON: {"symphony_id": str, "windows": {"30": {...}, "90": {...},
    "365": {...}}, "est_annual_friction_drag_pct": float}. A symphony with zero
    exit_triggers rows is a valid, honest empty-state (200 with zeroed windows),
    not a 404. An unexpected lookup failure degrades to the same zeroed shape
    rather than a 500.
    """
    import autotuner  # noqa: PLC0415 -- lazy per CC-2 precedent (app.py:3119), keeps Optuna deps off module load

    symphony_id = request.args.get("symphony_id", "").strip()
    if not symphony_id:
        return jsonify({"error": "symphony_id is required"}), 400

    _zeroed_windows = {w: {"exit_count": 0, "coverage_days": 0} for w in (30, 90, 365)}
    try:
        stats = database.get_exit_turnover_stats(symphony_id)
        drag_pct = database.compute_est_annual_friction_drag_pct(
            stats, autotuner.SIM_EXIT_FRICTION_PCT
        )
    except Exception:
        _daemon_log.debug("exit_turnover: stats lookup failed for %s", symphony_id, exc_info=True)
        stats = _zeroed_windows
        drag_pct = 0.0

    return jsonify(
        {
            "symphony_id": symphony_id,
            "windows": {str(k): v for k, v in stats.items()},
            "est_annual_friction_drag_pct": drag_pct,
        }
    )


@app.route("/api/candidate-alert")
def candidate_alert():
    """Return the header candidate-alert badge count + latest weekly-run status.

    AC-2: new_valid_count — count of NEW, UNVIEWED weekly-suggestion candidates
    (ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER, verdict=='ADOPT_CANDIDATE' only —
    see database.get_candidate_alert_new_valid_count for the verdict-classification
    trace; KEEP_INCUMBENT/REJECT_VETO_FAILED never count).
    AC-3: last_run — the latest weekly-batch aggregate (ran_at/evaluated/survivors),
    visible even at survivors==0 so a rejected-everything run still proves the
    subsystem is alive. None when no weekly-suggestion row has ever been written.
    AC-6: never raises — a DB accessor failure degrades to the honest empty state
    (new_valid_count=0, last_run=None), still 200.

    Read-only (both accessors use get_ro_connection — architecture constraint 5).
    Auth: covered by the global _auth_before_request hook (AC-8) — no additional
    decorator needed, same as guard_alpha_summary.
    """
    try:
        new_valid_count = database.get_candidate_alert_new_valid_count()
    except Exception:
        _daemon_log.debug("candidate_alert: new_valid_count lookup failed", exc_info=True)
        new_valid_count = 0

    try:
        last_run = database.get_candidate_alert_last_run()
    except Exception:
        _daemon_log.debug("candidate_alert: last_run lookup failed", exc_info=True)
        last_run = None

    return jsonify({"new_valid_count": new_valid_count, "last_run": last_run})


@app.route("/api/candidate-alert/mark-viewed", methods=["POST"])
def candidate_alert_mark_viewed():
    """Advance the candidate-alert viewed-marker so currently-visible survivors stop badging.

    AC-5: the marker is server-computed only (database.mark_candidate_alert_viewed
    takes no arguments) — any caller-supplied observation id in the request body is
    ignored, so a malicious/buggy client cannot set the marker to an arbitrary value.
    Idempotent — a repeat call never raises and never regresses the marker.

    Advisory-only write: NOT gated by _SETTINGS_WRITE_ALLOWLIST (that allowlist is
    exclusively for the separate /api/settings env-key write path) and never touches
    LIVE_EXECUTION. CSRF is enforced by the global _csrf_before_request @before_request
    hook (app.py:439-443) — not called here (same convention as the strategy-builder
    /run route, app.py:4544-4546; save_symphony_settings's explicit call is
    redundant/historical, not the pattern to copy).
    """
    last_viewed_observation_id = database.mark_candidate_alert_viewed()
    return jsonify({"status": "ok", "last_viewed_observation_id": last_viewed_observation_id})


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
            ret_by_time = {r[0]: r[2] for r in rows}
        except Exception:
            held_by_time = {}
            ret_by_time = {}

        if symphony_data:
            if held_by_time:
                symphony_data = [
                    {**pt, "held": held_by_time.get(pt.get("time"))} for pt in symphony_data
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
    accounts = [{"uuid": uuid, "label": label} for uuid, label in pairs if uuid]
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
                f"[{datetime.now().strftime('%H:%M:%S')}] Forcing EOD Analysis for {prev_date_str}..."  # noqa: E501  # un-wrappable long line
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
                bot_state,
                prev_date_str,
                account_uuids,
                is_forced=True,
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
                f"[{datetime.now().strftime('%H:%M:%S')}] Resending Discord Report for {prev_date_str}..."  # noqa: E501  # un-wrappable long line
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
    stats = analytics.get_history_summary(days=days, base_dir=analytics._POST_MORTEMS_DIR)
    stats["window_days"] = days

    # AC-3 (Finding 3): todays_exits is empty every trading day until the 15:54 ET
    # post-mortem write — not just on a day-1 droplet.  Backfill from exit_triggers
    # filtered to the CURRENT ET trading day, shaped exactly as history.js consumes
    # (ts / symphony_name / reason / detail — the same shape the post-mortem path
    # emits).  A zero-exit day stays honestly empty, and the windowed trigger_count
    # is never overwritten by the feed length (total_saved/win_rate derive from the
    # same windowed post-mortem entries).
    if not stats.get("todays_exits"):
        try:
            _today_et = datetime.now(_ET).date().isoformat()
            _conn = database.get_connection()
            try:
                # AC-6 (MAPERF-06): the Detail column has ONE semantic across both
                # sources — saved-alpha (guard-alpha pp), matching the post-mortem
                # path's saved_pct_guard_alpha (analytics.py get_history_summary).
                # `at_return` alone (the raw exit-level return) is a DIFFERENT
                # quantity under the same label; pair it with the symphony's latest
                # current_return (same shadow-subquery pattern as the strip
                # fallback below) so detail = at_return - current_return.
                try:
                    _rows = _conn.execute(
                        "SELECT t.symphony_id, t.ts_et, t.at_return, t.triggered_reason, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t WHERE substr(t.ts_et, 1, 10) = ? "
                        "ORDER BY t.ts_utc DESC",
                        (_today_et,),
                    ).fetchall()
                except Exception:
                    # shadow_history may not exist on a minimal/legacy DB (a fresh
                    # droplet before the first shadow-history cycle ever writes a
                    # row) — degrade to the raw exit_triggers columns so the row
                    # still renders; _guard_alpha_detail below still emits an
                    # honest None (never the pre-AC-6 raw-at_return-as-detail
                    # regression this fix removes).
                    _rows = [
                        (_sid, _ts_et, _at_ret, _reason, None)
                        for _sid, _ts_et, _at_ret, _reason in _conn.execute(
                            "SELECT symphony_id, ts_et, at_return, triggered_reason "
                            "FROM exit_triggers WHERE substr(ts_et, 1, 10) = ? "
                            "ORDER BY ts_utc DESC",
                            (_today_et,),
                        ).fetchall()
                    ]
            finally:
                _conn.close()
            if _rows:
                try:
                    _name_map = {
                        _sid: _entry.get("name")
                        for _sid, _entry in database.load_state().items()
                        if isinstance(_entry, dict) and _entry.get("name")
                    }
                except Exception:
                    _name_map = {}

                def _guard_alpha_detail(at_return, current_return):
                    if at_return is None or current_return is None:
                        return None
                    try:
                        return float(at_return) - float(current_return)
                    except (TypeError, ValueError):
                        return None

                stats["todays_exits"] = [
                    {
                        "ts": (r[1] or "").split("T")[-1],
                        "symphony_id": r[0],
                        "symphony_name": _name_map.get(r[0]) or r[0],
                        "reason": r[3],
                        "detail": _guard_alpha_detail(r[2], r[4]),
                    }
                    for r in _rows
                ]
        except Exception:
            _daemon_log.debug("get_history: exit_triggers fallback failed", exc_info=True)

    return jsonify(stats)


# --- 2b. History Tab ---
@app.route("/history")
def history_page():
    """Render the Guard Alpha History tab (read-only operator surface)."""
    return render_template(
        "history.html",
        active_route="history",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
    )


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
          "live_returns": [...],    # if-held: the still-held Composer account (current_return)
          "shadow_returns": [...],  # Planet-Stopper-exited counterfactual (shadow_return)
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

    raw_days = request.args.get("days", "60")
    # AC-5 (MAPERF-03, cross-plan correction): the Performance tab's YTD button
    # sends the literal token "ytd" (not a computed calendar-days-since-Jan-1
    # count) — resolved here to a Jan-1 CALENDAR cutoff via the same
    # analytics._window_cutoff_date helper /api/hero-chart and /api/strip use.
    # Every OTHER value on this param (the six numeric buttons: 30/60/90/125/
    # 252/1260) stays a deliberate TRADING-day count by design — only YTD's
    # contract changes; the numeric buttons are untouched.
    is_ytd = raw_days == "ytd"
    if is_ytd:
        days: int | str = "ytd"
    else:
        try:
            days = int(raw_days)
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

    # "ytd" fetches the FULL series and slices it to the Jan-1 cutoff (both
    # scopes); every other value fetches exactly `days` trailing trading days
    # (unchanged trading-day-count contract for the six numeric buttons).
    _fetch_days = None if is_ytd else days

    if scope == "aggregate":
        # Finding 4: the aggregate series is the CANONICAL value-weighted portfolio
        # series from shadow_history — the same series /api/hero-chart compounds —
        # never the post-mortem trigger arrays (a selection-biased exit-snapshot
        # event sample that dropped zero-trigger days and contradicted the Overview
        # chart on the same screen).  Every shadow_history trading day appears.
        dates, live_returns, shadow_returns = [], [], []
        try:
            _series = analytics.get_portfolio_bot_and_held_daily_returns(days=_fetch_days)
            if is_ytd:
                _series = _slice_series_by_window_cutoff(_series, "ytd")
            if _series is not None:
                # Producer returns (dates, bot, held); the payload vocabulary is
                # live_returns = if-held (held), shadow_returns = PS-exited (bot) —
                # the mapping every performance.js label + the docstring agree on.
                dates, shadow_returns, live_returns = _series
        except Exception:
            _daemon_log.debug("api_performance: canonical shadow series failed", exc_info=True)
    else:
        # AC-3 (MA-6/MAPERF-01): source the per-symphony series from shadow_history
        # per-day rows — the per-symphony analogue of the aggregate's canonical
        # continuous source (analytics.get_symphony_bot_and_held_daily_returns) —
        # NEVER the post-mortem trigger arrays (a selection-biased exit-snapshot
        # event sample that annualizes a handful of trigger days as if they were
        # that many consecutive trading days).
        dates, live_returns, shadow_returns = [], [], []
        try:
            _sym_series = analytics.get_symphony_bot_and_held_daily_returns(
                symphony_id, days=_fetch_days
            )
            if is_ytd:
                _sym_series = _slice_series_by_window_cutoff(_sym_series, "ytd")
            if _sym_series is not None:
                dates, shadow_returns, live_returns = _sym_series
        except Exception:
            _daemon_log.debug("api_performance: per-symphony shadow series failed", exc_info=True)

    # AC-2/AC-4 (MA-7/MAPERF-02): the day-1-droplet fallbacks below are
    # AGGREGATE-ONLY. A scope=symphony request for a symphony with zero
    # shadow_history rows must render an honest empty state — never the whole
    # PORTFOLIO's non-empty series mislabeled under that symphony's name (both
    # fallbacks were previously unconditional).

    # AC-2: when the series is still empty (day-1 droplet), fall back to
    # shadow_history for the series so the chart is non-empty from day one.
    # The insufficient_history / quantstats-min-obs guard is unchanged.
    if not dates and scope == "aggregate":
        try:
            _fallback = analytics.get_portfolio_bot_and_held_daily_returns()
            if _fallback is not None:
                # (dates, bot, held) -> held is live_returns (if-held), bot is
                # shadow_returns. This fallback was the ORIGINAL inverted surface.
                dates, shadow_returns, live_returns = _fallback
        except Exception:
            _daemon_log.debug("api_performance: shadow_history fallback failed", exc_info=True)

    # AC-2b: get_portfolio_bot_and_held_daily_returns() returns None when fewer than
    # 2 distinct trading days exist.  On a fresh droplet (day one), that guard fires
    # and leaves dates empty.  Fall back to the single-day seam so the chart is
    # non-empty even before the 2-day guard can pass.
    if not dates and scope == "aggregate":
        try:
            _single = analytics.get_single_day_shadow_returns()
            if _single is not None:
                # Same (dates, bot, held) -> (shadow_returns, live_returns) mapping.
                dates, shadow_returns, live_returns = _single
        except Exception:
            _daemon_log.debug(
                "api_performance: single-day shadow_history fallback failed", exc_info=True
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

    response_body = {
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
    if scope == "symphony":
        # AC-4 (F-023 / DE-PERFVIEW-ID-MISMATCH): distinguishes a genuine
        # no-data symphony (a real bot_state hash, just <threshold rows) from
        # a totally unrecognized symphony_id (stale/typo'd picker value) —
        # both produce observation_count == 0, but only the latter means the
        # id itself is wrong. Scoped to scope=symphony only; never emitted on
        # scope=aggregate responses.
        response_body["symphony_id_recognized"] = symphony_id in database.load_state()

    return jsonify(response_body)


@app.route("/api/performance/symphonies")
def api_performance_symphonies():
    """Sorted [{id, name}] list of live symphonies for the Performance-tab picker.

    Sourced from database.load_state() (bot_state, keyed by the Composer
    hash — the same hash<->name co-location pattern get_settings() already
    uses at app.py:3621-3628), NOT post-mortem history. F-023 /
    DE-PERFVIEW-ID-MISMATCH: the old post-mortem-derived list returned bare
    display NAMES as both label and value; that name was then sent as
    symphony_id into the hash-keyed shadow_history query and matched zero
    rows for every symphony. id is now the hash so it round-trips correctly
    into GET /api/performance?scope=symphony&symphony_id=.
    """
    state = database.load_state()
    symphonies = [
        {"id": sym_id, "name": data["name"]}
        for sym_id, data in state.items()
        if isinstance(data, dict) and "name" in data
    ]
    symphonies.sort(key=lambda entry: entry["name"])
    return jsonify({"symphonies": symphonies})


# --- 3. Account Liquidation ---
def perform_account_liquidation(account_id, key, secret, live_mode):
    headers = {
        "x-api-key-id": key,
        "authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphony-stats-meta"
    outcomes = {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            for idx, sym in enumerate(resp.json().get("symphonies", [])):
                if live_mode:
                    # F-003 residual: name/sell_url extraction moved INSIDE the
                    # per-symphony try below (was outside it) — a malformed
                    # entry (non-dict) raising AttributeError here used to
                    # escape to the OUTER try/except, aborting the ENTIRE
                    # panic-stop queue instead of isolating just this one
                    # symphony. name stays None if extraction itself raises,
                    # so the except branch can still key a FAILED outcome.
                    name = None
                    try:
                        name = sym.get("name")
                        sell_url = f"{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{sym.get('symphony_id') or sym.get('id')}/go-to-cash"  # noqa: E501  # un-wrappable long line
                        sell_resp = requests.post(sell_url, headers=headers, json={}, timeout=10)
                        if sell_resp.status_code in (200, 201, 202):
                            print(f"Liquidated {name} (HTTP {sell_resp.status_code})")
                            outcomes[name] = {"ok": True, "status": sell_resp.status_code}
                        else:
                            print(
                                f"LIQUIDATION FAILED {name} — HTTP {sell_resp.status_code} — {sell_resp.text[:200]}"  # noqa: E501  # un-wrappable long line
                            )
                            outcomes[name] = {
                                "ok": False,
                                "status": sell_resp.status_code,
                                "reason": sell_resp.text[:200],
                            }
                    except Exception as e:
                        _outcome_key = name if name is not None else f"<malformed-entry-{idx}>"
                        print(f"LIQUIDATION FAILED {_outcome_key} — {type(e).__name__}")
                        outcomes[_outcome_key] = {"ok": False, "reason": type(e).__name__}
                    time.sleep(1.5)
    except Exception as e:
        print(f"Liquidation Error: {e}")
    return outcomes


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
                    "content": f"EMERGENCY LIQUIDATION TRIGGERED on {account_id} at {ts_et} ET (live={live_mode})"  # noqa: E501  # un-wrappable long line
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


# ---------------------------------------------------------------------------
# Managed Sleeves (P3): sleeve/rule CRUD, arming ladder, disarm, envelope,
# condition-replay routes (feature-plans/managed-sleeves.md).
#
# These are operator-config writes -- create/arm/disarm a sleeve or rule --
# NEVER trade actions.  The only code path that ever places a broker order is
# the engine tick (sleeves.rules.runner, wired into alpha_bot_execution.main());
# these routes mutate sleeves/sleeve_rules status columns and read
# sleeve_orders/sleeve_rule_fires, matching the dashboard's prime directive
# that it is never a live-trade-action surface.  disarm_sleeve below is
# SYNCHRONOUS and DB-only (reverts sleeve/rule status to SHADOW immediately)
# -- it deliberately never calls sleeves.alpaca_orders.cancel_order or any
# other order-capable function, since doing so would trip the pre-existing
# whole-app.py containment invariant (tests/app/test_dashboard_no_order_path.py,
# which denylists cancel_order in every route except sell_account).  Actual
# cancellation of a disarmed sleeve's lingering open broker orders happens on
# the engine's very next tick (sleeves/tick_orchestrator.py).
# ---------------------------------------------------------------------------

_ARM_LIVE_PHRASE = "ARM LIVE TRADING"
_WIDEN_ENVELOPE_PHRASE = "WIDEN ENVELOPE"


@app.route("/api/sleeves", methods=["GET"])
def list_sleeves():
    """Return every sleeve row (AC-16 panel data source)."""
    return jsonify({"sleeves": database.get_all_sleeves()})


@app.route("/api/sleeves", methods=["POST"])
def create_sleeve_route():
    """Create a sleeve (AC-1). Starts SHADOW; capital_usd is fixed at creation."""
    payload = request.json or {}
    name = payload.get("name")
    if not name:
        return jsonify({"status": "error", "message": "name is required"}), 400
    capital_usd = payload.get("capital_usd")
    if capital_usd is None:
        return jsonify({"status": "error", "message": "capital_usd is required"}), 400
    try:
        capital_usd = float(capital_usd)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "capital_usd must be numeric"}), 400

    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    envelope = payload.get("envelope") or {}
    sleeve_id = database.create_sleeve(name, capital_usd, envelope_json=_json.dumps(envelope))
    return jsonify({"status": "success", "sleeve": database.get_sleeve(sleeve_id)})


@app.route("/api/sleeves/<int:sleeve_id>/rules", methods=["GET"])
def list_sleeve_rules(sleeve_id):
    """Return every rule for one sleeve (AC-16 panel data source)."""
    return jsonify({"rules": database.get_sleeve_rules_for_sleeve(sleeve_id)})


@app.route("/api/sleeves/<int:sleeve_id>/rules", methods=["POST"])
def create_sleeve_rule_route(sleeve_id):
    """Create a rule for one sleeve (AC-4/AC-6). Always born SHADOW."""
    if database.get_sleeve(sleeve_id) is None:
        return jsonify({"status": "error", "message": "sleeve not found"}), 404

    payload = request.json or {}

    from sleeves.rules import schema as sleeve_schema  # noqa: PLC0415

    validation = sleeve_schema.validate_rule_doc(payload)
    if not validation.valid:
        return jsonify(
            {
                "status": "error",
                "message": "rule doc failed schema validation",
                "errors": [{"field": e.field, "message": e.message} for e in validation.errors],
            }
        ), 400

    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    # AC-6: every rule is BORN in SHADOW regardless of any "mode" the client
    # sent in the create payload -- arming is a separate ceremony (see the
    # arm route below).
    rule_id = database.create_sleeve_rule(
        sleeve_id, payload.get("name", ""), json_doc=_json.dumps(payload), mode="SHADOW"
    )
    return jsonify({"status": "success", "rule": database.get_sleeve_rule(rule_id)})


@app.route("/api/sleeves/<int:sleeve_id>/rules/<int:rule_id>/arm", methods=["POST"])
def arm_sleeve_rule(sleeve_id, rule_id):
    """SHADOW -> PAPER arming (AC-13): rejected without >=1 recorded SHADOW fire.

    A successful arm ALSO promotes the owning sleeve's status SHADOW -> PAPER
    (PM design ruling, audit finding #3): sleeve status is the signal the
    tick's step-0 cleanup, the panel badge, and the Disarm control all key
    off, so a paper-armed rule inside a SHADOW-status sleeve had its orders
    cancelled every tick while rendering a calm standby badge with Disarm
    disabled. Promotion happens ONLY from SHADOW -- a LIVE sleeve is never
    demoted by arming one more rule, and an arm while PAUSED_RECONCILIATION
    is refused outright (409) so a money-truth pause can never be cleared as
    a side effect of arming. disarm_sleeve below is the inverse (sleeve + all
    rules back to SHADOW).

    Never touches SLEEVE_LIVE_EXECUTION/ALPACA_LIVE_* -- PAPER stays
    structurally confined to the paper host via sleeves.alpaca_orders.resolve_host.
    """
    rule_row = database.get_sleeve_rule(rule_id)
    if rule_row is None or rule_row.get("sleeve_id") != sleeve_id:
        return jsonify({"status": "error", "message": "rule not found"}), 404

    payload = request.json or {}
    if payload.get("target_mode") != "PAPER":
        return jsonify(
            {"status": "error", "message": "arm only supports target_mode=PAPER (SHADOW->PAPER)"}
        ), 400

    sleeve_status = (database.get_sleeve(sleeve_id) or {}).get("status")
    if sleeve_status == "PAUSED_RECONCILIATION":
        return jsonify(
            {
                "status": "error",
                "message": "sleeve is PAUSED_RECONCILIATION — resolve the reconciliation "
                "breach before arming (arming must never clear a money-truth pause)",
            }
        ), 409

    # Mode-filtered COUNT, never any() over get_sleeve_rule_fires' limited
    # page (review gap G8): 100+ newer PAPER fires from a previous armed life
    # would push every SHADOW fire out of the page and spuriously 400 a
    # legitimately re-armable rule.
    has_shadow_fire = database.get_sleeve_rule_fire_count(rule_id, mode_at_fire="SHADOW") > 0
    if not has_shadow_fire:
        return jsonify(
            {
                "status": "error",
                "message": "AC-13: no recorded SHADOW evaluation for this rule yet — arm rejected",
            }
        ), 400

    database.update_sleeve_rule_mode(rule_id, "PAPER")
    if sleeve_status == "SHADOW":
        database.update_sleeve_status(sleeve_id, "PAPER")
    return jsonify({"status": "success", "rule": database.get_sleeve_rule(rule_id)})


@app.route("/api/sleeves/<int:sleeve_id>/arm-live", methods=["POST"])
def arm_sleeve_live(sleeve_id):
    """PAPER -> LIVE panic-flow ceremony (AC-14), modeled on sell_account's
    6-gate chain (app.py, sell_account): confirm id + exact phrase, then
    live keys + SLEEVE_LIVE_EXECUTION -- impossible by construction without
    both. Every genuine ceremony attempt (gates 1-4 passed) is audited via
    Discord + an ERROR log entry regardless of whether gates 5/6 subsequently
    block the arm.
    """
    if database.get_sleeve(sleeve_id) is None:
        return jsonify({"status": "error", "message": "sleeve not found"}), 404

    payload = request.json or {}
    confirm_sleeve_id = payload.get("confirm_sleeve_id")
    confirm_phrase = payload.get("confirm_phrase")

    # Gates 1-4: confirmation validation before any env/credential check.
    if not confirm_sleeve_id:
        return jsonify({"status": "error", "message": "confirm_sleeve_id is required"}), 400
    if confirm_sleeve_id != sleeve_id:
        return jsonify(
            {"status": "error", "message": "confirm_sleeve_id does not match sleeve_id"}
        ), 400
    if not confirm_phrase:
        return jsonify({"status": "error", "message": "confirm_phrase is required"}), 400
    try:
        phrase_matches = secrets.compare_digest(str(confirm_phrase), _ARM_LIVE_PHRASE)
    except TypeError:
        phrase_matches = False
    if not phrase_matches:
        return jsonify(
            {"status": "error", "message": f"confirm_phrase must be exactly {_ARM_LIVE_PHRASE!r}"}
        ), 400

    env_vars = dotenv_values(ENV_FILE_PATH)
    ts_et = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")

    # Audit: Discord alert + ERROR log on every genuine ceremony attempt
    # (gates 1-4 passed) regardless of live-keys/flag outcome below --
    # mirrors sell_account's "audit always fires" contract.
    discord_url = env_vars.get("DISCORD_WEBHOOK_URL", "")
    if discord_url:
        try:
            requests.post(
                discord_url,
                json={"content": f"SLEEVE LIVE-ARM CEREMONY on sleeve {sleeve_id} at {ts_et} ET"},
                timeout=5,
            )
        except Exception:
            pass
    _daemon_log.error("SLEEVE LIVE-ARM CEREMONY attempted on sleeve %s at %s ET", sleeve_id, ts_et)

    live_key = (env_vars.get("ALPACA_LIVE_KEY") or "").strip()
    live_secret = (env_vars.get("ALPACA_LIVE_SECRET") or "").strip()
    if not (live_key and live_secret):
        return jsonify(
            {
                "status": "error",
                "message": "ALPACA_LIVE_KEY/ALPACA_LIVE_SECRET are not both configured "
                "— LIVE arming is impossible by construction",
            }
        ), 400

    sleeve_live_execution = (env_vars.get("SLEEVE_LIVE_EXECUTION", "False") or "False").lower() in (
        "true",
        "1",
        "yes",
    )
    if not sleeve_live_execution:
        return jsonify({"status": "error", "message": "SLEEVE_LIVE_EXECUTION is not enabled"}), 400

    database.update_sleeve_status(sleeve_id, "LIVE")
    return jsonify({"status": "success", "sleeve": database.get_sleeve(sleeve_id)})


@app.route("/api/sleeves/<int:sleeve_id>/disarm", methods=["POST"])
def disarm_sleeve(sleeve_id):
    """One-click per-sleeve kill switch (AC-12). SYNCHRONOUS, DB-only, and
    route-local: reverts the sleeve's own status and every one of its rules'
    mode back to SHADOW immediately -- autonomy stops right away; re-arming
    requires the arm ceremony again.

    This route deliberately never calls sleeves.alpaca_orders.cancel_order
    (or any order-capable function) -- doing so would trip the pre-existing
    whole-app.py containment invariant (tests/app/test_dashboard_no_order_path.py,
    which denylists cancel_order in every route except sell_account).
    Cancelling a disarmed sleeve's lingering open (non-terminal) broker
    orders is the ENGINE's job instead: sleeves/tick_orchestrator.py cancels
    any non-terminal sleeve_orders row it finds for a SHADOW-status sleeve on
    the very next tick (see tests/sleeves/test_tick_orchestrator.py) --
    positions and broker-side stops are never touched either way.
    """
    if database.get_sleeve(sleeve_id) is None:
        return jsonify({"status": "error", "message": "sleeve not found"}), 404

    database.update_sleeve_status(sleeve_id, "SHADOW")
    for rule in database.get_sleeve_rules_for_sleeve(sleeve_id):
        database.update_sleeve_rule_mode(rule["id"], "SHADOW")

    return jsonify({"status": "success", "sleeve": database.get_sleeve(sleeve_id)})


# Reuses database.get_daily_turnover_usd's / sleeves/tick_orchestrator.py's
# exact terminal-status denylist so "is this order done, one way or another"
# stays consistent with the rest of the codebase's Alpaca-status
# classification. "RESERVED" (the pre-ack sleeve_orders default, broker
# hasn't responded yet) is deliberately NOT in this tuple, so a single
# "status not in this tuple" check covers both an accepted-but-unfilled
# order and a still-RESERVED pre-ack row with no separate branch needed.
_DELETE_TERMINAL_ORDER_STATUSES = (
    "filled",
    "canceled",
    "expired",
    "replaced",
    "done_for_day",
    "rejected",
)


@app.route("/api/sleeves/<int:sleeve_id>/delete", methods=["POST"])
def delete_sleeve_route(sleeve_id):
    """Delete a sleeve (AC-16 delete control). Refuses unless flat -- a
    sleeve holding any nonzero position OR any non-terminal (still-open)
    order is never deleted (delete never liquidates, plan Edge Cases:
    "refuse unless flat"). The order-status check exists because a position
    is only ever created by ledger.apply_fill() -- an order the broker has
    accepted but not yet filled, or a still-RESERVED pre-ack row, produces
    zero positions and would otherwise read as falsely flat (s3-review BLOCK
    finding against the initial 821b385 GREEN) even though it's a live,
    unrecoverable broker exposure: once the sleeve row is gone,
    database.get_all_sleeves() never includes it again, so nothing would
    ever poll/cancel/reconcile that order for the rest of the process's
    life. Ledger reconstruction mirrors _build_sleeves_panel_context's own
    zero-new-schema mechanism.
    """
    sleeve_row = database.get_sleeve(sleeve_id)
    if sleeve_row is None:
        return jsonify({"status": "error", "message": "sleeve not found"}), 404

    from sleeves import ledger as sleeve_ledger  # noqa: PLC0415

    try:
        order_history = database.get_sleeve_order_history(sleeve_id)
        ledger_state = sleeve_ledger.reconstruct_from_history(
            sleeve_row.get("capital_usd") or 0.0, order_history
        )
        has_open_position = any(pos.qty != 0 for pos in ledger_state.positions.values())
        has_non_terminal_order = any(
            order.get("status") not in _DELETE_TERMINAL_ORDER_STATUSES for order in order_history
        )
    except Exception:
        # Fail closed: an unreconstructable ledger/order-history read can't
        # prove the sleeve is flat -- refuse the delete rather than risk
        # losing a real position's or a live order's only record.
        has_open_position = True
        has_non_terminal_order = True

    if has_open_position or has_non_terminal_order:
        return jsonify(
            {
                "status": "error",
                "message": "sleeve holds an open position or a still-open order — "
                "delete refused (delete never liquidates)",
            }
        ), 409

    database.delete_sleeve(sleeve_id)
    return jsonify({"status": "success"})


@app.route("/api/sleeves/<int:sleeve_id>/envelope", methods=["POST"])
def update_sleeve_envelope_route(sleeve_id):
    """Envelope widen/narrow (AC-3): narrowing applies immediately; widening
    requires the same confirm-id + confirm-phrase ceremony shape as arm-live.
    """
    sleeve_row = database.get_sleeve(sleeve_id)
    if sleeve_row is None:
        return jsonify({"status": "error", "message": "sleeve not found"}), 404

    payload = request.json or {}
    new_envelope = payload.get("envelope")
    if not isinstance(new_envelope, dict):
        return jsonify({"status": "error", "message": "envelope (object) is required"}), 400

    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    import sleeves.envelope  # noqa: PLC0415

    old_envelope = _json.loads(sleeve_row.get("envelope_json") or "{}")
    widened = sleeves.envelope.is_envelope_widened(old_envelope, new_envelope)

    if widened:
        confirm_sleeve_id = payload.get("confirm_sleeve_id")
        confirm_phrase = payload.get("confirm_phrase")
        if not confirm_sleeve_id or confirm_sleeve_id != sleeve_id:
            return jsonify(
                {
                    "status": "error",
                    "message": "AC-3: widening the envelope requires confirm_sleeve_id to match",
                }
            ), 400
        if not confirm_phrase:
            return jsonify(
                {
                    "status": "error",
                    "message": "AC-3: widening the envelope requires confirm_phrase",
                }
            ), 400
        try:
            phrase_matches = secrets.compare_digest(str(confirm_phrase), _WIDEN_ENVELOPE_PHRASE)
        except TypeError:
            phrase_matches = False
        if not phrase_matches:
            return jsonify(
                {
                    "status": "error",
                    "message": f"confirm_phrase must be exactly {_WIDEN_ENVELOPE_PHRASE!r}",
                }
            ), 400

    database.update_sleeve_envelope(sleeve_id, _json.dumps(new_envelope))
    return jsonify({"status": "success", "sleeve": database.get_sleeve(sleeve_id)})


@app.route("/api/sleeves/<int:sleeve_id>/rules/<int:rule_id>/replay", methods=["GET"])
def replay_sleeve_rule(sleeve_id, rule_id):
    """Condition-replay diagnostic (AC-18) — NEVER an arming input, NEVER a
    P&L claim (fill-simulating backtest is explicitly out of scope). Runs the
    rule's OWN condition tree through the real sleeves.rules.conditions/senses
    modules -- the same engine a live tick uses -- over the trailing N daily
    bars, returning the dates it WOULD have fired plus the sensed values at
    each.

    P3 ships no historical daily-bar source of its own (out of this route's
    committed scope — see feature-plans/managed-sleeves.md); with no cached
    closes available, every indicator sense correctly reports
    insufficient_history (senses.py's own fail-safe contract), so the
    response is always an honest empty result rather than a fabricated fire.
    """
    rule_row = database.get_sleeve_rule(rule_id)
    if rule_row is None or rule_row.get("sleeve_id") != sleeve_id:
        return jsonify({"status": "error", "message": "rule not found"}), 404

    days_raw = request.args.get("days", "30")
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "days must be an integer"}), 400
    if not (1 <= days <= 60):
        return jsonify({"status": "error", "message": "days must satisfy 1 <= days <= 60"}), 400

    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality
    from datetime import timedelta  # noqa: PLC0415 — stdlib, lazy for locality

    from sleeves.rules import conditions as sleeve_conditions  # noqa: PLC0415
    from sleeves.rules import senses as sleeve_senses  # noqa: PLC0415
    from sleeves.rules.runner import _collect_sense_keys  # noqa: PLC0415

    try:
        doc = _json.loads(rule_row.get("json_doc") or "{}")
    except (TypeError, ValueError):
        doc = {}

    condition_tree = doc.get("if")
    would_have_fired: list[dict] = []

    if isinstance(condition_tree, dict) and condition_tree.get("op"):
        sense_keys = _collect_sense_keys(condition_tree)
        today_et = datetime.now(_ET).date()
        for offset in range(days, 0, -1):
            as_of_date = today_et - timedelta(days=offset)
            now_et = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 16, 0, tzinfo=_ET)
            sense_ctx = sleeve_senses.SenseContext(
                now_et=now_et,
                sleeve_row={},
                closes=[],  # no historical daily-bar source wired in P3 — see docstring
                fred_cache={},
                as_of=as_of_date,
            )
            sensed = {key: sleeve_senses.resolve_sense(key, ctx=sense_ctx) for key in sense_keys}
            eval_result = sleeve_conditions.evaluate_condition(condition_tree, sensed)
            if eval_result.fireable:
                would_have_fired.append(
                    {
                        "date": as_of_date.isoformat(),
                        "sensed": {k: v.value for k, v in sensed.items()},
                    }
                )

    return jsonify(
        {
            "status": "success",
            "label": "condition_replay_diagnostic",
            "rule_id": rule_id,
            "days": days,
            "would_have_fired": would_have_fired,
        }
    )


# --- 3b. Settings Page Route ---
@app.route("/settings")
def settings_page():
    """Render the Settings page (read-only template; mutations via /api/settings)."""
    return render_template(
        "settings.html",
        active_route="settings",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
    )


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
        "help": "Monte Carlo threshold for arming the trailing stop. Lower = bot waits longer before defending.",  # noqa: E501  # un-wrappable long line
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
        "unit": "%",
        "kind": "pct",
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
        {"id": norm, "name": raw_names.get(norm, norm)} for norm in sorted(symphony_names)
    ]

    # symphony_overrides mirrors symphonies_data but is the canonical field name
    # expected by settings.js for the override editor pane.
    return jsonify(
        {
            "globals": globals_data,
            "secrets": secrets_data,
            "symphonies": symphonies_data,
            "symphony_overrides": symphonies_data,
            "symphonies_list": symphonies_list,
            "param_meta": _ALGO_PARAM_META,
        }
    )


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
            return jsonify(
                {
                    "status": "error",
                    "message": f"Rejected keys not in settings allowlist: {sorted(rejected)}",
                }
            ), 400

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
    """Return per-symphony modal state: live_mode, global_live, parameters, locked_vars, advisor observations.  # noqa: E501  # un-wrappable long line

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
        if (
            database.normalize_name(sym_key) == symphony_name
            or normalized_sym_name == symphony_name
        ):
            obs_symphony_id = normalized_sym_name
            break
    advisor_observations = database.get_advisor_observations_for_symphony(obs_symphony_id)

    return jsonify(
        {
            "live_mode": live_mode,
            "global_live": global_live,
            "parameters": strategy.get("params", {}),
            "locked_vars": strategy.get("locked_vars", []),
            "advisor_observations": advisor_observations,
        }
    )


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
                return jsonify(
                    {
                        "status": "error",
                        "message": "live_mode must be a boolean (true/false), not a string or other type.",  # noqa: E501  # un-wrappable long line
                    }
                ), 400
            # isinstance(True, int) is True in Python so bool is already covered above;
            # for bare ints, only 0 and 1 are valid — reject out-of-range values.
            if not isinstance(live_mode_raw, bool) and live_mode_raw not in (0, 1):
                return jsonify(
                    {
                        "status": "error",
                        "message": "live_mode integer must be 0 or 1.",
                    }
                ), 400

        if live_mode_raw is True or live_mode_raw == 1:
            if not payload.get("confirm"):
                return jsonify(
                    {
                        "status": "error",
                        "message": (
                            "confirm=true is required to enable live trading. "
                            "A bare toggle click must never persist live mode."
                        ),
                    }
                ), 400
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
_REAL_POST_MORTEM_DATES = frozenset(
    {
        "2025-05-12",
        "2025-05-14",
        "2025-05-16",
        "2026-05-11",
        "2026-05-14",
        "2026-05-15",
        "2026-05-16",
        "2026-05-17",
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
    }
)


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
        _daemon_log.info(
            "flush_resync: identified %d symphony state entries for reset", len(symphonies_reset)
        )
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
            _daemon_log.info(
                "flush_resync: background reset wrote %d symphony state entries", _reset_count
            )
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
        "flush_resync: deleted %d synthetic files, kept %d real, reset %d symphonies, resync_ok=%s, errors=%d",  # noqa: E501  # un-wrappable long line
        len(deleted),
        len(kept),
        len(symphonies_reset),
        resync_ok,
        len(errors),
    )

    return jsonify(
        {
            "status": "ok" if not errors else "partial",
            "deleted_count": len(deleted),
            "kept_count": len(kept),
            "deleted": deleted,
            "kept": kept,
            "symphonies_reset": symphonies_reset,
            "symphonies_reset_count": len(symphonies_reset),
            "composer_resync": resync_ok,
            "errors": errors,
        }
    )


# --- 5. AI Advisor Routes ---


def _translate_backtest_error(err: "str | None") -> "str | None":
    """Translate raw backtest error strings into operator-readable messages.

    Composer's inline-backtest endpoint returns an nginx 413 HTML page when
    the serialised symphony tree exceeds the request body limit.  Passing
    that raw HTML into the JSON response exposes markup to the operator and
    leaks internal server details.  AC-9c: translate 413 errors to a plain
    explanation; leave all other error strings unchanged.

    Shared by both evaluate routes (logic-changes + asset-swaps) so the
    translation is a single code path, not duplicated per-route.
    """
    if err is None:
        return None
    if "413" in err or "<html>" in err.lower():
        return (
            "Symphony too large to backtest inline — exceeds Composer's "
            "request size limit. The change is advisory only; no backtest "
            "result is available for this symphony."
        )
    return err


# AC-6 (F2/N=1 honesty — feature-plans/advisor-remediation-r1.md): the two
# operator-initiated Evaluate routes (asset-swaps, logic-changes) each route
# exactly ONE candidate through evaluate_candidate_batch — c(N=1)=1.0 makes the
# BHY/Yekutieli multiple-testing correction a mathematical no-op (see
# autotuner.benjamini_hochberg_adjust). The gate's shared
# SURVIVOR_OVERFITTING_CAVEAT text unconditionally carries "BHY/Yekutieli FDR"
# branding on an ADOPT verdict; at N=1 that branding falsely implies a
# multi-candidate correction ran. Strip it and disclose the real N=1 shape
# instead. The N>1 weekly scheduler paths (suggest_swaps / suggest_logic_
# changes) never call these two routes and keep their real FDR/Yekutieli
# labeling untouched.
_N1_HONESTY_NOTE = "single-candidate check — no multiple-testing correction applies (N=1)"


def _n1_honest_caveats(caveats: "list[str] | None") -> list[str]:
    """Strip FDR/Yekutieli-branded caveat text and append the N=1 honesty
    disclosure. Shared by both operator-initiated Evaluate routes (AC-6) —
    never called from the weekly N>1 scheduler paths."""
    filtered = [c for c in (caveats or []) if "FDR" not in c and "Yekutieli" not in c]
    filtered.append(_N1_HONESTY_NOTE)
    return filtered


# AC-9 (F3, Gap C — near-zero statistical power at reachable fold lengths):
# audit F3 chained the codebase's own compute_sortino_tstat -> compute_
# haircut_pvalue -> benjamini_hochberg_adjust on synthetic data and found
# near-zero detection power at the gate's own verified T=13 fold-length
# floor (FOLD_TRANSFORM_MIN_TOTAL_DAYS=65*0.20), and confirmed the finding
# still holds at a fixture-verified T=121 real-symphony anchor (hash
# INfCn3eKsu6i4oTTqdUp, series_len=606) — even there, N=12 batch-corrected
# detection is 0% for every economically-plausible effect size. Deliberately
# in app.py, not backtest_gate_engine.py (collision-avoidance with
# r1-engine's concurrent AC-4/5/17 work there, per the locked contract) —
# this is a UI-caveat threshold, not a gate-math constant; the gate's
# accept/reject logic is unaffected. Value = the fixture-verified anchor
# itself (T=121): the audit describes even that longer fold as only "weak"
# power, so anything at or below it earns the caveat.
MIN_POWER_FOLD_DAYS = 121

# Caveat text for AC-9 — additive to SURVIVOR_OVERFITTING_CAVEAT (the
# engine's own selection-bias disclosure), never a replacement. Rendered
# only for SURVIVOR cards — a rejected candidate's fold length is moot, it
# didn't clear the gate either way.
_LOW_POWER_CAVEAT = (
    "This candidate cleared the statistical gate, but the underlying "
    "backtest window is short enough that statistical power to detect a "
    "genuine edge is low. Treat as a candidate for further scrutiny, not a "
    "proven result."
)


def _low_power(validation_days: "int | None") -> bool:
    """True when the validation fold is short enough that detection power
    is near-zero per audit F3 — see MIN_POWER_FOLD_DAYS. None (fold length
    unknown) is never flagged — absence of data is not evidence of low
    power."""
    return validation_days is not None and validation_days < MIN_POWER_FOLD_DAYS


# AC-1/AC-2/AC-3/AC-16 (attribution honesty + coherence): every model-name
# badge/copy string in the AI Advisor UI reads a resolved accessor value at
# render time — never a hardcoded "Opus"/"Fable" literal. This map is
# display-only humanization (mirrors the map-known/fallback-to-raw idiom in
# advisors/prism_render.py) — an UNKNOWN model ID (a future model, or a test
# monkeypatch marker) passes through unchanged rather than being dropped, so
# a new model or a test fixture can never silently vanish from the badge.
_MODEL_DISPLAY_NAMES = {
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-fable-5": "Claude Fable 5",
    "claude-mythos-5": "Claude Mythos 5",
}


def _humanize_model_name(model_id: str) -> str:
    """Map a raw model ID to a display-ready name; unmapped IDs (a future
    model, or a test's monkeypatched marker string) pass through unchanged."""
    return _MODEL_DISPLAY_NAMES.get(model_id, model_id)


def _build_verification_count_line(summary: "dict") -> str:
    """Format the MARKET_PRISM_VERIFICATION summary counts into a compact,
    human-readable status line for the Overview tab's Numeric Verification
    section (2026-07 redesign, replaces a flat wall of 25+ pass badges).

    e.g. "25 verified · 2 unverifiable", or with actionable catches present,
    "23 verified · 2 unverifiable · 1 flagged · 1 overridden" — zero flagged/
    overridden counts are omitted so a clean run reads as just two numbers.
    Returns "" when there are no checks at all (the no-numeric-claims /
    no-verifiable-claims verdict states), where "0 verified · 0 unverifiable"
    would be noise rather than signal.
    """
    n_checks = summary.get("n_checks", 0) or 0
    if not n_checks:
        return ""
    n_pass = summary.get("n_pass", 0) or 0
    n_flagged = summary.get("n_flagged", 0) or 0
    n_overridden = summary.get("n_overridden", 0) or 0
    n_unverifiable = summary.get("n_unverifiable", 0) or 0
    parts = [f"{n_pass} verified", f"{n_unverifiable} unverifiable"]
    if n_flagged:
        parts.append(f"{n_flagged} flagged")
    if n_overridden:
        parts.append(f"{n_overridden} overridden")
    return " · ".join(parts)


# Verdict -> the 4 existing check-badge CSS classes (/review PR #92): the
# verdict pill reuses --pass/--flagged/--overridden/--unverifiable rather than
# duplicating the same 4 --studio-* color pairs under 5 new class names.
_VERDICT_BADGE_CLASS = {
    "clean": "prism-verify-badge--pass",
    "flags-detected": "prism-verify-badge--flagged",
    "overrides-detected": "prism-verify-badge--overridden",
    "no-verifiable-claims": "prism-verify-badge--unverifiable",
    "no-numeric-claims": "prism-verify-badge--unverifiable",
}


def _build_verdict_display(verdict: "str | None") -> "tuple[str, str]":
    """Map a MARKET_PRISM_VERIFICATION verdict string to (label, css_class) for
    the Overview tab's verdict pill (2026-07 redesign, /review PR #92).

    A recognized verdict (clean/flags-detected/overrides-detected/
    no-verifiable-claims/no-numeric-claims) renders with its label verbatim and
    the matching check-badge class. An unrecognized or missing verdict must
    NOT silently render as "no-numeric-claims" (that would misleadingly assert
    "nothing was checked" when the data doesn't say so) — it renders a neutral
    unverifiable-class pill labeled with the raw string, or "unknown" when
    verdict itself is falsy.
    """
    label = verdict if verdict else "unknown"
    css_class = _VERDICT_BADGE_CLASS.get(verdict, "prism-verify-badge--unverifiable")
    return label, css_class


def _bounded_json_preview(value, max_chars: int) -> str:
    """Serialize `value` to indented JSON and truncate to max_chars with a
    '... truncated (N total chars)' marker when it exceeds the bound. Never
    raises — falls back to str(value) on a serialization error. Empty
    string for a None value.
    """
    if value is None:
        return ""
    try:
        import json as _json  # noqa: PLC0415

        text = _json.dumps(value, indent=2)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... truncated ({len(text)} total chars)"
    return text


@app.route("/ai-advisor", methods=["GET"])
def ai_advisor_tab():
    """Render the single unified AI Advisor page with all 5 in-place tab panels.

    Consolidates the data formerly spread across 5 separate GET routes
    (Overview, Correlations, Asset Swaps, Logic Changes, Chat) into one
    server-side render.  Tab switching is handled in-place by JS (no navigation).

    Template context:
      observations       — advisor_observations rows for the Overview panel
      correlation_matrix — PairResult list for the Correlations panel
      as_of              — ISO timestamp for the matrix
      crisis_caveat      — instability warning string
      insufficient_data  — True when no correlation pairs available
      no_api_key         — True when Composer credentials absent (swaps + logic)
      symphonies         — known symphony IDs (swap + logic forms)
      chat_available     — True when ANTHROPIC_API_KEY is set
    """
    # ------------------------------------------------------------------ #
    # Overview panel: advisor observations                                  #
    # ------------------------------------------------------------------ #
    observations: list[dict] = []
    for role in _ADVISOR_ROLES:
        observations.extend(
            database.get_advisor_observations_for_role(role, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT)
        )
    seen: set = set()
    deduped_obs: list[dict] = []
    for obs in observations:
        if obs["id"] not in seen:
            seen.add(obs["id"])
            deduped_obs.append(obs)
    # Suppress pure feature-off stubs (NOT_APPLICABLE / feature_flag=off).
    deduped_obs = [
        obs
        for obs in deduped_obs
        if not (
            obs.get("verdict") == "NOT_APPLICABLE"
            and isinstance(obs.get("raw_response"), dict)
            and obs["raw_response"].get("feature_flag") == "off"
        )
    ]
    observations = deduped_obs[:_ADVISOR_OBSERVATIONS_PAGE_LIMIT]

    # Stamp _preview_text onto each non-MARKET_PRISM observation so the template
    # can render a concise human-readable cell without dumping raw JSON.
    # MARKET_PRISM rows are handled separately (show verdict only).
    try:
        from advisors.prism_render import humanize_obs_preview as _humanize_obs  # noqa: PLC0415

        for _obs in observations:
            if _obs.get("advisor_role") != "MARKET_PRISM":
                _obs["_preview_text"] = _humanize_obs(_obs.get("raw_response"))
    except Exception:
        pass  # Humanization failure must never crash the route.

    # ------------------------------------------------------------------ #
    # Correlations panel: pairwise return matrix                           #
    # Lazy import keeps the module off the live 1-minute execution path.   #
    # ------------------------------------------------------------------ #
    correlation_matrix: list = []
    crisis_caveat: str = ""
    try:
        from advisors import correlation_diagnostic as _corr_diag  # noqa: PLC0415

        _history = analytics.get_history_with_cache_invalidation(
            base_dir=analytics._POST_MORTEMS_DIR
        )
        _sym_ids = analytics.list_available_symphonies(_history)
        # Date-keyed (not positional-list) so compute_pairwise_correlations
        # aligns each pair by shared calendar date, not raw list index —
        # per-symphony series are sparse trigger-day returns with unrelated
        # calendars, so positional alignment pairs unrelated dates.
        _series_dict: dict[str, dict[str, float]] = {}
        for _sym_id in _sym_ids:
            _dates, _live_rets, _shadow = analytics.compute_per_symphony_returns(_history, _sym_id)
            if _live_rets:
                _series_dict[_sym_id] = dict(zip(_dates, _live_rets))
        correlation_matrix = _corr_diag.compute_pairwise_correlations(_series_dict)
        crisis_caveat = _corr_diag.CRISIS_CAVEAT
    except Exception:
        pass  # Correlations panel renders as insufficient_data on any error.

    insufficient_data: bool = len(correlation_matrix) == 0

    # ------------------------------------------------------------------ #
    # Asset Swaps + Logic Changes panels: API key availability             #
    # ------------------------------------------------------------------ #
    no_api_key: bool = True
    try:
        from advisors.asset_swap_engine import _has_composer_key  # noqa: PLC0415

        no_api_key = not _has_composer_key()
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # Symphony list for the swap + logic forms                             #
    # ------------------------------------------------------------------ #
    symphonies: list[str] = []
    try:
        _hist2 = analytics.get_history_with_cache_invalidation(base_dir=analytics._POST_MORTEMS_DIR)
        symphonies = analytics.list_available_symphonies(_hist2)
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # Chat panel: key presence only (value never passes to template)       #
    # ------------------------------------------------------------------ #
    chat_available: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # ------------------------------------------------------------------ #
    # Market Prism panel: prefetch latest MARKET_PRISM summary for the   #
    # Overview tab.  Always renders (empty state when None).              #
    # Read-only — never reruns the pipeline from the UI.                  #
    # ------------------------------------------------------------------ #
    market_prism_summary: dict | None = None
    try:
        market_prism_summary = database.get_latest_market_prism_summary()
    except Exception:
        pass  # Empty state rendered by template on None.

    # ------------------------------------------------------------------ #
    # Additively merge MARKET_PRISM_SOURCES article_corpus into the       #
    # per_lens_digest for url-bearing lenses (DE-PRISM-SOURCES-001 v2).  #
    # Matched by run_id — no stale citation bleed if run_ids differ.     #
    # Deep-copy before mutating so the original DB row object is never   #
    # modified in-place (safe for shared references in tests / caches).  #
    # ------------------------------------------------------------------ #
    if market_prism_summary:
        try:
            import copy as _copy  # noqa: PLC0415

            market_prism_summary = _copy.deepcopy(market_prism_summary)
            _mp_raw = market_prism_summary.get("raw_response") or {}
            if isinstance(_mp_raw, str):
                import json as _json  # noqa: PLC0415

                _mp_raw = _json.loads(_mp_raw)
                market_prism_summary["raw_response"] = _mp_raw
            _mp_run_id = _mp_raw.get("run_id") if isinstance(_mp_raw, dict) else None
            if _mp_run_id:
                _sources_row = database.get_latest_market_prism_sources_for_run(_mp_run_id)
                if _sources_row is not None:
                    _src_raw = _sources_row.get("raw_response") or {}
                    _src_pld = (
                        _src_raw.get("per_lens_digest", {}) if isinstance(_src_raw, dict) else {}
                    )
                    _mp_pld = (
                        _mp_raw.get("per_lens_digest", {}) if isinstance(_mp_raw, dict) else {}
                    )
                    for _src_lens, _src_lens_data in _src_pld.items():
                        if isinstance(_src_lens_data, dict) and isinstance(
                            _mp_pld.get(_src_lens), dict
                        ):
                            _corpus = _src_lens_data.get("article_corpus")
                            if _corpus:
                                _mp_pld[_src_lens]["article_corpus"] = _corpus
        except Exception as _merge_exc:
            # Log type-only at WARNING — no exc args/message to avoid leaking citation
            # content into logs; honest empty-state is still rendered (no re-raise).
            _daemon_log.warning(
                "market-prism sources merge skipped: %s",
                type(_merge_exc).__name__,
            )

    # ------------------------------------------------------------------ #
    # Additively fetch the MARKET_PRISM_VERIFICATION row (numeric fact-   #
    # check overlay, DE-PRISM-NUMERIC-VERIFY-001, AC-10) and attach       #
    # per-check annotations for the Overview render.  Matched by run_id — #
    # no stale bleed if run_ids differ (mirrors the SOURCES merge guard). #
    # Deep-copy before reading/attaching so the original DB row object   #
    # is never mutated in-place (defense-in-depth — market_prism_summary #
    # is already a fresh copy from the SOURCES block above).             #
    # Honest empty-state: stays None when no VERIFICATION row exists yet #
    # for this run_id.                                                    #
    # ------------------------------------------------------------------ #
    market_prism_verification: dict | None = None
    if market_prism_summary:
        try:
            # No deepcopy here — market_prism_summary is already a fresh copy
            # from the SOURCES block above (see the block comment above).
            _mpv_raw = market_prism_summary.get("raw_response") or {}
            if isinstance(_mpv_raw, str):
                import json as _json  # noqa: PLC0415

                _mpv_raw = _json.loads(_mpv_raw)
                market_prism_summary["raw_response"] = _mpv_raw
            _mpv_run_id = _mpv_raw.get("run_id") if isinstance(_mpv_raw, dict) else None
            if _mpv_run_id:
                _verification_row = database.get_latest_market_prism_verification_for_run(
                    _mpv_run_id
                )
                if _verification_row is not None:
                    # raw_response is pre-deserialized by
                    # database._parse_advisor_observation_row — always a dict or
                    # None here, never a JSON string. Do not "restore" a
                    # str-coercion branch for this row.
                    _ver_raw = _verification_row.get("raw_response") or {}
                    _ver_checks = _ver_raw.get("checks", []) if isinstance(_ver_raw, dict) else []
                    _annotated_checks = []
                    for _chk in _ver_checks:
                        if not isinstance(_chk, dict):
                            continue
                        _annotated = dict(_chk)
                        # Annotate both actionable classifications — flagged and
                        # overridden both carry a real cited/ground-truth pair
                        # from _build_check (only pass/unverifiable don't have a
                        # meaningful diff to report). 2026-07 UI redesign: these
                        # are the only checks rendered as individual badges.
                        if _annotated.get("classification") in ("flagged", "overridden"):
                            _annotated["annotation"] = (
                                f"council cited {_annotated.get('cited_value')}; "
                                f"source says {_annotated.get('ground_truth_value')}"
                            )
                        _annotated_checks.append(_annotated)
                    _ver_summary = (
                        _ver_raw.get("summary", {}) if isinstance(_ver_raw, dict) else {}
                    ) or {}
                    _ver_verdict = _ver_raw.get("verdict") if isinstance(_ver_raw, dict) else None
                    _verdict_label, _verdict_class = _build_verdict_display(_ver_verdict)
                    market_prism_verification = {
                        "checks": _annotated_checks,
                        "summary": _ver_summary,
                        "verdict": _ver_verdict,
                        # Compact verdict-first summary (2026-07 redesign): the
                        # day-to-day signal is the verdict pill + a short count
                        # line, not a wall of 25+ pass/unverifiable badges.
                        # Computed here (testable Python) rather than in Jinja.
                        "verdict_label": _verdict_label,
                        "verdict_class": _verdict_class,
                        "count_line": _build_verification_count_line(_ver_summary),
                        "actionable_checks": [
                            _c
                            for _c in _annotated_checks
                            if _c.get("classification") in ("flagged", "overridden")
                        ],
                    }
        except Exception as _verify_exc:
            # Log type-only at WARNING — no exc args/message; honest empty-state
            # is still rendered (no re-raise).
            _daemon_log.warning(
                "market-prism verification merge skipped: %s",
                type(_verify_exc).__name__,
            )

    # Pre-humanize per_lens_digest summaries so the template never sees raw JSON.
    # Council prose passes through unchanged; lens_pipeline JSON is humanized to
    # readable text.  Null summaries become an honest empty-state string so the
    # template's {% if _lens.get('summary') %} guard always produces a visible paragraph.
    if market_prism_summary:
        try:
            from advisors.prism_render import (
                humanize_lens_summary as _humanize_lens,  # noqa: PLC0415
            )

            _raw_resp = market_prism_summary.get("raw_response", {})
            if isinstance(_raw_resp, str):
                import json as _json  # noqa: PLC0415

                _raw_resp = _json.loads(_raw_resp)
            _per_lens = _raw_resp.get("per_lens_digest", {}) if isinstance(_raw_resp, dict) else {}
            for _ln, _le in _per_lens.items():
                if isinstance(_le, dict):
                    _le["summary"] = _humanize_lens(_ln, _le)
        except Exception:
            pass  # Humanization failure must never crash the route.

    # ------------------------------------------------------------------ #
    # Strategy Builder panel: prefetch STRATEGY_BUILDER observations +    #
    # build per-card M6 artifact dicts for the Discuss affordance.        #
    # Lazy import keeps advisor_chat off the live 1-minute execution path. #
    # ------------------------------------------------------------------ #
    sb_observations: list[dict] = []
    sb_card_artifacts: dict = {}
    try:
        sb_observations = list(
            reversed(database.get_advisor_observations_for_role("STRATEGY_BUILDER"))
        )
    except Exception:
        pass

    if sb_observations:
        try:
            from advisors.advisor_chat import (
                CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS as _sb_chat_max,  # noqa: PLC0415
            )
        except Exception:
            _sb_chat_max = 500
        for _obs in sb_observations:
            _rr = _obs.get("raw_response")
            if not isinstance(_rr, dict):
                _rr = {}
            _rules_text = _rr.get("rules_text") or ""
            if isinstance(_rules_text, str) and len(_rules_text) > _sb_chat_max:
                _rules_text = _rules_text[:_sb_chat_max]
            _obs_id = _obs.get("id")
            if _obs_id is None:
                continue
            sb_card_artifacts[_obs_id] = {
                "artifact_type": "strategy_proposal",
                "artifact_id": _rr.get("candidate_id") or _obs.get("subject_id", ""),
                "symphony_id": _obs.get("subject_id", ""),
                "template_id": _rr.get("template_id"),
                "tickers": _rr.get("tickers"),
                "rules_text": _rules_text,
                "gate_verdict": _rr.get("gate_decision") or _obs.get("verdict"),
                "n_candidates": _rr.get("n_candidates"),
                "n_survivors": _rr.get("n_survivors"),
                "fdr_adjusted_threshold": _rr.get("fdr_adjusted_threshold") or _rr.get("fdr_q"),
                "screen_verdict": _rr.get("screen_verdict"),
                "rejected_reason": _rr.get("rejected_reason"),
                "cagr": _rr.get("cagr"),
                "sharpe": _rr.get("sharpe"),
                "calmar": _rr.get("calmar"),
                "correlation_vs_live": _rr.get("correlation_vs_live"),
                "blended_drawdown": _rr.get("blended_drawdown"),
            }
            # Inject sparkline points directly onto obs for template rendering.
            _obs["sparkline_points"] = _rr.get("equity_curve_downsampled")

    # ------------------------------------------------------------------ #
    # Strategy incubation gate: live-join each survivor's persisted        #
    # raw_response.candidate_hash against the incubation ledger, computed  #
    # FRESH on every request (AC-5 amendment -- advisor_observations rows  #
    # are append-only/immutable, so status is never read from a frozen     #
    # field; see .claude/tdd-handoff.md "raw_response contract"). Mirrors  #
    # the RF-1 per_lens_digest in-place-stamp precedent above. A survivor  #
    # with no candidate_hash (pre-feature row) or no matching ledger row   #
    # (admission failed/capped) is left un-stamped -- the template renders #
    # no chip rather than fabricating a status.                            #
    # ------------------------------------------------------------------ #
    if sb_observations:
        try:
            _incubation_by_hash = {
                _row.get("candidate_hash"): _row for _row in database.get_incubation_overview()
            }
        except Exception:
            _incubation_by_hash = {}
        for _obs in sb_observations:
            _rr = _obs.get("raw_response")
            if not isinstance(_rr, dict):
                continue
            _c_hash = _rr.get("candidate_hash")
            _ledger_row = _incubation_by_hash.get(_c_hash) if _c_hash else None
            if _ledger_row is None:
                continue
            try:
                _badge = _incubation_badge(_ledger_row)
            except Exception:
                continue
            _obs["_incubation_badge_label"] = _badge["label"]
            _obs["_incubation_badge_modifier"] = _badge["modifier"]

    # ------------------------------------------------------------------ #
    # Retirement Recommendations panel (Cycle 2a, AC-10): prefetch the    #
    # latest persisted RETIREMENT_RECOMMENDATION rows via the SAME helper #
    # GET /api/retirement-recommendations uses. Read-only -- never        #
    # recomputes/reruns advisors.retirement_recommender from this render. #
    # Each entry is the persisted raw_response dict verbatim (the         #
    # authoritative schema) -- no renaming/translation layer.             #
    #                                                                      #
    # Shared lazy bot_state loader (PR#140 review findings 4/5; Cycle 2d     #
    # AC-4/finding 5 additionally threads it into _fetch_retirement_        #
    # recommendations()'s own bot_state need below): the checklist          #
    # assembly, the frontrunner identity resolution further down, AND       #
    # _refresh_retirement_display_names (via the call immediately below)    #
    # all need the full bot_state dict -- this loads it AT MOST ONCE per    #
    # request, on first actual need, shared by all consumers. A request     #
    # with no retirement recommendations, no approved retirement cards,     #
    # AND no pending frontrunner proposals still costs zero load_state()    #
    # calls here (preserves the pre-existing lazy-I/O guarantee -- see the  #
    # frontrunner block's own F5/F9(c) rationale).                          #
    # ------------------------------------------------------------------ #
    _ai_advisor_bot_state_loaded = False
    _ai_advisor_bot_state: dict = {}

    def _ensure_ai_advisor_bot_state() -> dict:
        nonlocal _ai_advisor_bot_state_loaded, _ai_advisor_bot_state
        if not _ai_advisor_bot_state_loaded:
            try:
                _ai_advisor_bot_state = database.load_state()
            except Exception:
                _ai_advisor_bot_state = {}
            _ai_advisor_bot_state_loaded = True
        return _ai_advisor_bot_state

    retirement_recommendations: list[dict] = []
    try:
        # Cycle 2d (AC-4, PR#140 2nd /code-review finding 5): pass the
        # shared closure so _refresh_retirement_display_names's bot_state
        # need (inside _fetch_retirement_recommendations) is absorbed into
        # the SAME memoized load the checklist/frontrunner blocks below
        # use, instead of independently calling database.load_state() again.
        retirement_recommendations = _fetch_retirement_recommendations(
            bot_state_getter=_ensure_ai_advisor_bot_state
        )
    except Exception:
        pass  # Empty-state rendered by template on [].

    # ------------------------------------------------------------------ #
    # Retirement approval status + display names: both now resolved       #
    # INSIDE _fetch_retirement_recommendations() itself -- approval_status #
    # via _join_retirement_approval_status (Cycle 2c, AC-6) and            #
    # candidate_name/sibling_name via _refresh_retirement_display_names    #
    # (PR#140 review finding 3) -- so this panel and GET /api/retirement-  #
    # recommendations always agree on both. Every rec already carries a   #
    # fresh "approval_status" and fresh "candidate_name"/"sibling_name"   #
    # by the time it reaches here; the template reads those fields        #
    # directly (no separate panel-local resolution needed).               #
    # ------------------------------------------------------------------ #
    _ret_any_approved = any(
        _rec.get("approval_status") == "approved" for _rec in retirement_recommendations
    )

    # Checklist assembly (AC-6/AC-7 glue): deterministic, no LLM, built
    # ONLY for approved cards -- never inside the approve route itself
    # (AC-5: "Approve writes status ONLY"). Shares the lazy bot_state
    # loader defined above with the frontrunner block below.
    if _ret_any_approved:
        _ret_bot_state = _ensure_ai_advisor_bot_state()
        try:
            from advisors.retirement_checklist import build_checklist  # noqa: PLC0415
        except Exception:
            build_checklist = None
        for _rec in retirement_recommendations:
            if _rec["approval_status"] != "approved":
                continue
            if build_checklist is None:
                _rec["_checklist"] = None
                continue
            try:
                _rec["_checklist"] = build_checklist(_rec, _ret_bot_state)
            except Exception:
                _rec["_checklist"] = None

    # ------------------------------------------------------------------ #
    # Frontrunner Builder panel: prefetch pending frontrunner_proposals    #
    # rows (AC-9-route). Shared by both proposal_source values             #
    # ('frontrunner_builder' and 'strategy_builder_retrofit') — one query, #
    # the template branches per-card on proposal_source. candidate_tree    #
    # (the full spliced symphony, potentially 8,000+ nodes) is popped and  #
    # replaced with a bounded truncated preview string before it ever      #
    # reaches the template — never rendered as a live dict in context.     #
    # ------------------------------------------------------------------ #
    _FR_TREE_PREVIEW_MAX_CHARS = 4000
    _FR_OVERLAY_PREVIEW_MAX_CHARS = 4000
    frontrunner_proposals: list[dict] = []
    # F10 (Revise 2): default mirrors advisors.frontrunner_builder.
    # OVERLAY_NOT_RECORDED_TEXT verbatim — bound here so a builder-import
    # failure below can never leave this name unbound at render_template().
    overlay_not_recorded_text = "overlay not recorded for this proposal"
    try:
        frontrunner_proposals = database.get_pending_frontrunner_proposals()
        # F8/F10 (Revise 2): shared cross-section helpers from
        # advisors.frontrunner_builder — CC-2 lazy import, isolated in its
        # OWN dedicated try (same F5 rationale as load_state() below): an
        # import failure must never skip the per-row loop body (which is
        # what actually pops candidate_tree out of template context).
        _fr_resolve_display_name = None
        try:
            from advisors.frontrunner_builder import (  # noqa: PLC0415
                OVERLAY_NOT_RECORDED_TEXT as _fr_overlay_not_recorded_text_const,
            )
            from advisors.frontrunner_builder import (
                resolve_incumbent_display_name as _fr_resolve_display_name,
            )

            overlay_not_recorded_text = _fr_overlay_not_recorded_text_const
        except Exception:
            _fr_resolve_display_name = None

        # Resolved ONCE outside the per-row loop (not once per row) — mirrors
        # advisors.frontrunner_builder.approve_frontrunner_proposal's own
        # NAME<-hash lookup pattern. A load_state() failure degrades identity
        # resolution to the raw-hash fallback for every row, never skips the
        # per-row loop body entirely (which would leave candidate_tree
        # un-popped). Also skipped outright when there are zero pending
        # proposals (F9(c)) — wasted I/O with no possible benefit since the
        # loop never iterates. Shares the lazy bot_state loader defined near
        # the top of this function with the retirement checklist block
        # above (PR#140 review findings 4/5) — at most one load_state()
        # call total for both, never a redundant second one when both need it.
        _fr_bot_state: dict = {}
        if frontrunner_proposals:
            _fr_bot_state = _ensure_ai_advisor_bot_state()
        for _fr_p in frontrunner_proposals:
            _fr_tree = _fr_p.pop("candidate_tree", None)
            _fr_p["candidate_tree_preview"] = _bounded_json_preview(
                _fr_tree, _FR_TREE_PREVIEW_MAX_CHARS
            )

            # Incumbent display-name resolution (F8, Revise 2) — shared
            # source of truth with advisors.frontrunner_builder.
            # approve_frontrunner_proposal; honest raw-hash fallback both
            # when unresolvable AND when the import above failed.
            if _fr_resolve_display_name is not None:
                _fr_p["_incumbent_display_name"] = _fr_resolve_display_name(
                    _fr_bot_state, _fr_p.get("symphony_id")
                )
            else:
                _fr_p["_incumbent_display_name"] = _fr_p.get("symphony_id")

            # AC-6: a non-dict metrics_json (e.g. a parsed JSON list) must
            # degrade to {} before the template ever touches `m.xxx` — the
            # route must always return 200, never a 500.
            if not isinstance(_fr_p.get("metrics_json"), dict):
                _fr_p["metrics_json"] = {}

            # Overlay-tree preview — same transform as candidate_tree_preview
            # above, but never popped: the template still needs
            # m.replaced_node_id / m.overlay_summary reachable via metrics_json.
            _fr_overlay_tree = _fr_p["metrics_json"].get("overlay_tree")
            _fr_p["overlay_tree_preview"] = _bounded_json_preview(
                _fr_overlay_tree, _FR_OVERLAY_PREVIEW_MAX_CHARS
            )
    except Exception:
        pass  # Empty-state rendered by template on [].

    # AC-1/AC-2/AC-3 (attribution honesty): resolved at request time so a
    # monkeypatch or an env-var change takes effect without a daemon restart —
    # same pattern as every other accessor-driven value on this page.
    # ADVISOR_SUGGESTION_MODEL (model_config) drives Strategy Builder's
    # built-new label + the SB run-controls-note (AC-1/AC-3). ADVISOR_
    # SYNTHESIS_MODEL (ai_advisor.resolve_advisor_model) drives Chat's badge
    # + the Market Prism attribution (AC-1/AC-2) — a separate, independent
    # knob per AC-16.
    advisor_suggestion_model = _humanize_model_name(model_config.get_advisor_suggestion_model())
    advisor_synthesis_model = _humanize_model_name(ai_advisor.resolve_advisor_model())

    return render_template(
        "ai_advisor.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        observations=observations,
        correlation_matrix=correlation_matrix,
        as_of=datetime.now(_ET).isoformat(),
        crisis_caveat=crisis_caveat,
        insufficient_data=insufficient_data,
        no_api_key=no_api_key,
        symphonies=symphonies,
        chat_available=chat_available,
        sb_observations=sb_observations,
        sb_card_artifacts=sb_card_artifacts,
        market_prism_summary=market_prism_summary,
        market_prism_verification=market_prism_verification,
        retirement_recommendations=retirement_recommendations,
        frontrunner_proposals=frontrunner_proposals,
        overlay_not_recorded_text=overlay_not_recorded_text,
        advisor_suggestion_model=advisor_suggestion_model,
        advisor_synthesis_model=advisor_synthesis_model,
    )


@app.route("/ai-advisor/correlations", methods=["GET"])
def ai_advisor_correlations():
    """Redirect to the unified /ai-advisor page (in-place tabs migration).

    The correlations content is now a tab panel on /ai-advisor.  Old bookmarks
    and links redirect cleanly rather than 404ing.
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


@app.route("/ai-advisor/asset-swaps", methods=["GET"])
def ai_advisor_asset_swaps():
    """Redirect to the unified /ai-advisor page (in-place tabs migration).

    The asset-swaps content is now a tab panel on /ai-advisor.
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


@app.route("/ai-advisor/asset-swaps/evaluate", methods=["POST"])
def ai_advisor_asset_swaps_evaluate():
    """Operator-initiated swap evaluation endpoint (AC-2.1).

    Accepts JSON: { symphony_id, from_ticker?, to_ticker?, objective_type? }.
    R2-3: from_ticker/to_ticker are now OPTIONAL. Supplying BOTH evaluates
    that exact pair (explicit-pair mode — byte-preserves the pre-R2-3
    response shape, additively gaining provenance/survivors_detail/
    rejected_detail). Supplying NEITHER lets the LLM-reasoned generator
    propose objective-directed swap pairs over the operator's real holdings
    + a validated tradeable universe (objective-only mode — array-shaped
    response mirroring the logic-changes route). Supplying exactly ONE
    ticker is an honest 200 error — never silently reinterpreted as either
    mode (team-lead's R2-3 contract ruling).

    Constructs a typed SwapObjective, injects the operator's real tree + live
    stats + 5 market-lens blocks via ai_advisor.build_reasoning_context (R2-3,
    mirrors R2-2's AC-1) for BOTH modes, fetches the baseline tree via
    symphony_logic, calls propose_operator_swap from advisors.asset_swap_engine,
    and returns the SwapRunResult fields as JSON.

    Never runs a live trade; never calls Composer write endpoints (AC-X1).
    Persistence (advisor_observation) is handled inside propose_operator_swap (AC-X3).

    Returns JSON with the swap result for rendering in the UI.
    """
    # R2-3 (AC-8): route-minted default provenance — present on EVERY return
    # path of this route, including the branches below that fire BEFORE the
    # reasoned engine is ever called (no Composer key, exactly-one-ticker,
    # missing symphony_id, hash-resolution failure, tree-fetch failure) and
    # the engine-call exception handler. evidence_injected defaults to the
    # all-absent manifest (ai_advisor._EMPTY_MANIFEST) — honest, since no
    # reasoning context was gathered on any of those paths, never a
    # placeholder. Mirrors the logic-changes route (R2-2) byte-for-byte. The
    # success path below instead reads the ENGINE's own provenance (which
    # reflects what build_reasoning_context actually found for this
    # symphony), falling back to this same default via a defensive
    # getattr+isinstance guard — never None.
    _default_provenance = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "asset-swap",
        "evidence_injected": dict(ai_advisor._EMPTY_MANIFEST),
        "run_id": str(uuid.uuid4()),
    }

    # Lazy imports (AC-X2 — keep asset_swap_engine off the live execution path).
    from advisors.asset_swap_engine import (  # noqa: PLC0415
        SwapObjective,
        _has_composer_key,
        propose_operator_swap,
    )
    from symphony_logic import fetch_symphony_score  # noqa: PLC0415

    if not _has_composer_key():
        return jsonify(
            {
                "error": "advisor unavailable: API key not configured",
                "provenance": _default_provenance,
            }
        ), 200

    body = request.get_json(silent=True) or {}
    symphony_id = str(body.get("symphony_id", "")).strip()
    from_ticker = str(body.get("from_ticker", "")).strip().upper()
    to_ticker = str(body.get("to_ticker", "")).strip().upper()
    # objective_type defaults to reduce_correlation for operator-initiated mode
    # (Gate-1 Resolution #2: every swap must be objective-directed; operator can
    # override via the optional form field).
    objective_type = str(body.get("objective_type", "reduce_correlation")).strip()

    if not symphony_id:
        return jsonify({"error": "symphony_id is required", "provenance": _default_provenance}), 200

    # R2-3 (AC-12): the two operator modes must be genuinely disjoint.
    # Checked BEFORE any composer_hash/DB lookup — an exactly-one-ticker
    # request must never fall through to a hash-resolution error instead of
    # this honest, pinned message.
    explicit_pair = bool(from_ticker) and bool(to_ticker)
    if bool(from_ticker) != bool(to_ticker):
        return jsonify(
            {
                "error": "supply both tickers for an explicit pair, or neither to let the advisor propose",  # noqa: E501  # pinned literal, un-wrappable
                "provenance": _default_provenance,
            }
        ), 200

    # AC-8: the payload carries the display NAME (from the analytics dropdown); the
    # Composer API needs the HASH.  Resolve NAME -> Composer hash via bot_state
    # (inverse of the AC-4 modal, which resolved hash -> name).
    # bot_state keys are Composer hashes; sym_data["name"] is the display name.
    # RC-6: fail loudly if the name can't resolve — the silent pass-through
    # (`composer_hash = symphony_id`) would backtest a display name against Composer
    # (which returns empty/404), masking the real problem as a silent no-result.
    composer_hash = None
    _bot_state = database.load_state()
    for _sym_key, _sym_data in _bot_state.items():
        if not isinstance(_sym_data, dict) or "name" not in _sym_data:
            continue
        if database.normalize_name(_sym_data["name"]) == database.normalize_name(symphony_id):
            composer_hash = _sym_key
            break

    if composer_hash is None:
        return jsonify(
            {
                "error": f"could not resolve name to a Composer hash: {symphony_id!r} not found in active symphonies",  # noqa: E501  # un-wrappable long line
                "provenance": _default_provenance,
            }
        ), 200

    raw_value = fetch_symphony_score(composer_hash)
    if not raw_value:
        return jsonify(
            {
                "error": f"could not fetch symphony tree for {symphony_id}",
                "provenance": _default_provenance,
            }
        ), 200

    # Construct a typed SwapObjective (Gate-1 Resolution #2 — no plain string objectives).
    objective = SwapObjective(
        objective_type=objective_type,
        target_pair=None,
        measured_value=0.0,
    )

    # R2-3 (AC-1 mirror): inject the operator's REAL tree + live stats + 5
    # market-lens blocks into the reasoned generator's prompt — same call
    # shape as the logic-changes route (R2-2) and Strategy Builder (R2-1).
    # Called unconditionally for BOTH modes: explicit-pair mode also passes
    # reasoning_context through as an optional steering hint (mirrors R2-2
    # retaining change_description as a hint alongside real context).
    reasoning_context, reasoning_manifest = ai_advisor.build_reasoning_context(
        symphony_id, objective, composer_symphony_id=composer_hash
    )

    # Mode 2 (explicit-pair) passes both tickers through; mode 3
    # (objective-only) omits them entirely so the engine's reasoned branch
    # fires (AC-12: the two modes must be genuinely disjoint at the call site).
    _pair_kwargs = (
        {"incumbent_asset": from_ticker, "candidate_asset": to_ticker} if explicit_pair else {}
    )

    try:
        run_result = propose_operator_swap(
            # Pass the Composer hash — engine uses it as the UUID for dvm_capital
            # unpacking in run_backtest (composer_backtest_client.py:269).
            # The engine's _persist_observation normalizes to a canonical name for
            # the advisor_observations DB key (RC-4 keying handled engine-side).
            symphony_id=composer_hash,
            score_tree=raw_value,
            objective=objective,
            reasoning_context=reasoning_context,
            reasoning_manifest=reasoning_manifest,
            **_pair_kwargs,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_asset_swaps_evaluate failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(exc).__name__, "provenance": _default_provenance}), 200

    # Build FDR metadata for the operator audit trail (AC-3.2, mirrors the
    # logic-changes route's identical derivation).
    gate_batch = run_result.gate_batch
    fdr_adjusted_threshold: float | None = None
    if gate_batch is not None:
        n = gate_batch.n_candidates or 1
        # Yekutieli c(n) = sum(1/k for k in 1..n) — same formula as autotuner._c_yekutieli.
        c_n = sum(1.0 / k for k in range(1, n + 1))
        fdr_adjusted_threshold = gate_batch.fdr_q / c_n if c_n > 0 else gate_batch.fdr_q

    def _swap_proposal_to_dict(p) -> dict:
        """Serialise a SwapProposalResult to a JSON-friendly dict (swap-flavored
        mirror of the logic-changes route's _proposal_to_dict — incumbent_asset/
        candidate_asset replace the tweak_* fields since SwapProposalResult
        already carries them as top-level attributes)."""
        gr = p.gate_result
        return {
            "candidate_id": p.candidate_id,
            "symphony_id": p.symphony_id,
            "objective_type": p.objective.objective_type if p.objective else None,
            "objective_rationale": p.objective_rationale,
            "incumbent_asset": p.incumbent_asset,
            "candidate_asset": p.candidate_asset,
            "baseline_stats": p.baseline_stats,
            "variant_stats": p.variant_stats,
            # Gate verdict (AC-3.3)
            "gate_decision": gr.verdict.decision if gr else None,
            "gate_reason": (
                gr.verdict.decision.replace("_", " ").title()
                if gr and gr.verdict.vetoes_passed
                else ("veto failed" if gr else None)
            ),
            "validation_days": gr.validation_days if gr else None,
            "oos_alpha": gr.oos_alpha if gr else None,
            "winner_p_adj": gr.winner_p_adj if gr else None,
            # AC-9: statistical-power flag, threshold in app.py only — never
            # duplicated client-side.
            "low_power": _low_power(getattr(gr, "validation_days", None)) if gr else False,
            # AC-7: pbo_veto / below_spy_alpha / oos_inferior_to_incumbent /
            # fdr_not_winner / None — the granular cause, distinct from the
            # coarse gate_reason title above (which collapses all veto
            # failures into "veto failed"). None on a genuine survivor.
            "rejection_reason": getattr(gr, "rejection_reason", None) if gr else None,
            # FDR metadata for audit trail (AC-3.2)
            "n_candidates": gate_batch.n_candidates if gate_batch else None,
            "fdr_q": gate_batch.fdr_q if gate_batch else None,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            # Caveats (mandatory for survivors, AC-3.3); N=1-honest per AC-6.
            "caveats": _n1_honest_caveats(p.caveats),
            # Apply guidance — plain text, no button (AC-X1)
            "apply_guidance": p.apply_guidance,
            "backtest_error": _translate_backtest_error(p.backtest_error),
            "data_warnings": p.data_warnings,
        }

    # AC-9: low_power's BOOLEAN was already computed above, but the caveat
    # TEXT must also be appended so the operator sees it as readable text —
    # additive on survivors_detail only (mirrors the logic-changes route's
    # identical post-processing loop).
    _survivors_detail = [_swap_proposal_to_dict(p) for p in run_result.survivors]
    for _survivor in _survivors_detail:
        if _survivor["low_power"]:
            _survivor["caveats"] = [*_survivor["caveats"], _LOW_POWER_CAVEAT]
    _rejected_detail = [_swap_proposal_to_dict(p) for p in run_result.rejected_candidates]

    # R2-3 (AC-5/AC-8): run-level provenance — read straight off
    # run_result.provenance (the engine's real 4-key contract), defensive
    # getattr+isinstance(dict) guard identical to the shipped SB/LC routes —
    # getattr's default alone is not enough against a bare Mock stand-in (it
    # auto-vivifies ANY attribute access into a child Mock); falls back to
    # the route-minted default instead of None.
    provenance = getattr(run_result, "provenance", None)
    if not isinstance(provenance, dict):
        provenance = _default_provenance

    if not explicit_pair:
        # Mode 3 (objective-only reasoned): array-shaped response — there may
        # be N candidates, so no single candidate_id/from_ticker/to_ticker
        # top-level field makes sense (mirrors the logic-changes route's shape).
        try:
            return jsonify(
                {
                    "message": run_result.message,
                    "survivors": len(run_result.survivors),
                    "no_api_key": run_result.no_api_key,
                    "survivors_detail": _survivors_detail,
                    "rejected_detail": _rejected_detail,
                    "provenance": provenance,
                }
            ), 200
        except Exception as _je:
            _daemon_log.error(
                "ai_advisor_asset_swaps_evaluate response serialization failed: %s",
                _je,
                exc_info=True,
            )
            return jsonify({"error": type(_je).__name__, "provenance": _default_provenance}), 200

    # Mode 2 (explicit-pair): byte-preserve every pre-R2-3 top-level key from
    # the first proposal (single-candidate operator-initiated mode) plus the
    # run-level message and gate batch metadata (AC-2.3 / AC-2.5), additively
    # gaining provenance + survivors_detail/rejected_detail (AC-12).
    proposal = run_result.proposals[0] if run_result.proposals else None
    gate_result = proposal.gate_result if proposal else None

    # AC-9 (r1-review Checkpoint-3 BLOCK finding): the low_power BOOLEAN was
    # already wired into gate_result above, but the actual CAVEAT TEXT was
    # never appended here — a True flag silently present in JSON, never
    # surfaced as operator-readable text, does not satisfy "survivor cards
    # carry a statistical-power caveat" (mirrors the SB route's existing
    # post-processing loop). Additive on a genuine survivor only — a
    # rejected candidate's fold length is moot, it didn't clear the gate
    # either way.
    _caveats = _n1_honest_caveats(proposal.caveats if proposal else None)
    if (
        gate_result
        and gate_result.verdict.decision == "ADOPT_CANDIDATE"
        and _low_power(getattr(gate_result, "validation_days", None))
    ):
        _caveats.append(_LOW_POWER_CAVEAT)

    try:
        return jsonify(
            {
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
                    # AC-9: statistical-power flag, threshold in app.py only —
                    # never duplicated client-side.
                    "low_power": _low_power(getattr(gate_result, "validation_days", None)),
                    # AC-7: pbo_veto / below_spy_alpha / oos_inferior_to_incumbent /
                    # fdr_not_winner / None — computed on every CandidateGateResult
                    # (backtest_gate_engine.py) but never threaded through this
                    # route until now. None on a genuine survivor — never
                    # fabricated (regression-guarded).
                    "rejection_reason": getattr(gate_result, "rejection_reason", None),
                }
                if gate_result
                else None,
                # Caveats (mandatory for survivors — SURVIVOR_OVERFITTING_CAVEAT),
                # N=1-honest per AC-6: FDR/Yekutieli branding stripped, replaced
                # with the real single-candidate disclosure; low-power text
                # appended above when applicable (AC-9).
                "caveats": _caveats,
                # Apply guidance — plain text, no button (AC-X1)
                "apply_guidance": proposal.apply_guidance if proposal else "",
                # AC-9c: translate raw nginx 413 HTML to a clean operator message.
                "backtest_error": _translate_backtest_error(proposal.backtest_error)
                if proposal
                else None,
                "data_warnings": proposal.data_warnings if proposal else [],
                # R2-3 additive keys (AC-9 / AC-12) — never remove/rename an
                # existing key above, only add.
                "provenance": provenance,
                "survivors_detail": _survivors_detail,
                "rejected_detail": _rejected_detail,
            }
        ), 200
    except Exception as _je:
        _daemon_log.error(
            "ai_advisor_asset_swaps_evaluate response serialization failed: %s",
            _je,
            exc_info=True,
        )
        return jsonify({"error": type(_je).__name__, "provenance": _default_provenance}), 200


@app.route("/ai-advisor/logic-changes", methods=["GET"])
def ai_advisor_logic_changes():
    """Redirect to the unified /ai-advisor page (in-place tabs migration).

    The logic-changes content is now a tab panel on /ai-advisor.
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


@app.route("/ai-advisor/logic-changes/evaluate", methods=["POST"])
def ai_advisor_logic_changes_evaluate():
    """Operator-initiated logic-change evaluation endpoint (AC-3.1).

    Accepts JSON: { symphony_id, objective_type?, change_description }.
    Builds a LogicChangeObjective, injects the operator's real tree + live
    stats + 5 market-lens blocks via ai_advisor.build_reasoning_context
    (R2-2, AC-1), fetches the baseline tree via symphony_logic, calls
    propose_operator_logic_change from advisors.logic_change_engine, and
    returns the LogicChangeRunResult fields as JSON.

    Never runs a live trade; never calls Composer write endpoints (AC-X1).
    Persistence (advisor_observation) is handled inside propose_operator_logic_change
    (AC-X3).

    The change_description is a plain-text operator input (e.g., "change momentum
    lookback from 20 to 10 days"), retained as a steering hint into the engine's
    LLM-reasoned generator (R2-2 — replaces the old deterministic heuristic
    parser). The LLM proposes objective-directed edits over the operator's
    ACTUAL tree; each edit's node_path/param_key is resolved against the real
    tree and structurally re-validated (symphony_schema.validate_tree) before
    backtest — an edit that doesn't resolve or fails validation is dropped
    with an honest reason, never fabricated. LLM unavailability or a
    malformed/empty proposal degrades to zero survivors, never a crash
    (AC-X5 isolation applies at the engine level, not here).

    Returns JSON with the logic-change result for rendering in the UI.
    """
    # R2-2 (AC-5): route-minted default provenance — present on EVERY return
    # path of this route, including the branches below that fire BEFORE the
    # reasoned engine is ever called (import failure, no Composer key,
    # missing input, hash-resolution failure, tree-fetch failure) and the
    # engine-call exception handler. evidence_injected defaults to the
    # all-absent manifest (ai_advisor._EMPTY_MANIFEST) — honest, since no
    # reasoning context was gathered on any of those paths, never a
    # placeholder. This is STRICTER than R2-1's SB route (which only carries
    # provenance on its success path) — team-lead ruling. The success path
    # below instead reads the ENGINE's own provenance (which reflects what
    # build_reasoning_context actually found for this symphony), falling
    # back to this same default via a defensive getattr+isinstance guard —
    # never None.
    _default_provenance = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "logic-change",
        "evidence_injected": dict(ai_advisor._EMPTY_MANIFEST),
        "run_id": str(uuid.uuid4()),
    }

    # Lazy imports (AC-X2 — keep logic_change_engine off the live execution path).
    try:
        from advisors.logic_change_engine import (  # noqa: PLC0415
            LogicChangeObjective,
            _has_composer_key,
            propose_operator_logic_change,
        )
        from symphony_logic import fetch_symphony_score  # noqa: PLC0415
    except ImportError as _ie:
        _daemon_log.error("ai_advisor_logic_changes_evaluate import failed: %s", _ie, exc_info=True)
        # D-1: surface only the error class, not str(_ie).
        return jsonify(
            {
                "error": f"advisor unavailable: {type(_ie).__name__}",
                "provenance": _default_provenance,
            }
        ), 200

    if not _has_composer_key():
        return jsonify(
            {
                "error": "advisor unavailable: API key not configured",
                "provenance": _default_provenance,
            }
        ), 200

    body = request.get_json(silent=True) or {}
    symphony_id = str(body.get("symphony_id", "")).strip()
    objective_type = str(body.get("objective_type", "reduce_drawdown")).strip()
    change_description = str(body.get("change_description", "")).strip()

    if not symphony_id or not change_description:
        return jsonify(
            {
                "error": "symphony_id and change_description are required",
                "provenance": _default_provenance,
            }
        ), 200

    # AC-8: same NAME->Composer-hash resolution as asset-swaps/evaluate.
    # RC-6: fail loudly if the name can't resolve — no silent pass-through.
    composer_hash = None
    _bot_state = database.load_state()
    for _sym_key, _sym_data in _bot_state.items():
        if not isinstance(_sym_data, dict) or "name" not in _sym_data:
            continue
        if database.normalize_name(_sym_data["name"]) == database.normalize_name(symphony_id):
            composer_hash = _sym_key
            break

    if composer_hash is None:
        return jsonify(
            {
                "error": f"could not resolve name to a Composer hash: {symphony_id!r} not found in active symphonies",  # noqa: E501  # un-wrappable long line
                "provenance": _default_provenance,
            }
        ), 200

    raw_value = fetch_symphony_score(composer_hash)
    if not raw_value:
        return jsonify(
            {
                "error": f"could not fetch symphony tree for {symphony_id}",
                "provenance": _default_provenance,
            }
        ), 200

    # Build a typed LogicChangeObjective (Gate-1 Resolution #2 — no plain-string objectives).
    objective = LogicChangeObjective(
        objective_type=objective_type,
        measured_value=0.0,
        rationale=change_description,
    )

    # R2-2 (AC-1): inject the operator's REAL tree + live stats + 5 market-lens
    # blocks into the reasoned generator's prompt — same call SB's route makes
    # (app.py's ai_advisor_strategy_builder_run, build_reasoning_context call).
    # symphony_id here is the operator-supplied normalized name (this route's
    # own id key); composer_hash is the Composer UUID used for the tree fetch
    # (project's AI Advisor Composer hash rule). NOTE: build_reasoning_context
    # re-fetches the tree internally (symphony_logic.get_condensed_logic ->
    # fetch_symphony_score) — a second /score read beyond raw_value above.
    # Accepted cost per the plan's "reuse build_reasoning_context verbatim"
    # directive (R2-1 shipped code, not touched here); logged as an R2
    # follow-up (let build_reasoning_context accept a pre-fetched tree),
    # not fixed in this cycle.
    reasoning_context, reasoning_manifest = ai_advisor.build_reasoning_context(
        symphony_id, objective, composer_symphony_id=composer_hash
    )

    # Delegate to the engine; pass change_description= so the engine's own
    # LLM-reasoned generator (generate_reasoned_logic_candidates) runs
    # internally, steered by change_description + reasoning_context (R2-2 —
    # replaces the old deterministic heuristic parser). When the reasoned
    # generator proposes nothing (LLM unavailable, malformed output, or no
    # edit resolves against the real tree) the engine sets backtest_error on
    # the proposal and returns zero survivors — no early-return needed here
    # (AC-X5 isolation applies at the engine level).
    try:
        run_result = propose_operator_logic_change(
            # Pass the Composer hash — engine uses it as the UUID for dvm_capital
            # unpacking in run_backtest (composer_backtest_client.py:269).
            # The engine's _persist_observation normalizes to a canonical name for
            # the advisor_observations DB key (RC-4 keying handled engine-side).
            symphony_id=composer_hash,
            score_tree=raw_value,
            tweak=None,
            objective=objective,
            change_description=change_description,
            reasoning_context=reasoning_context,
            reasoning_manifest=reasoning_manifest,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_logic_changes_evaluate failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(exc).__name__, "provenance": _default_provenance}), 200

    # Build FDR metadata for the operator audit trail (AC-3.2).
    gate_batch = run_result.gate_batch
    proposal = run_result.proposals[0] if run_result.proposals else None
    gate_result = proposal.gate_result if proposal else None

    # Adjusted p-value threshold = fdr_q / c(n), where c(n) is the Yekutieli
    # harmonic-sum correction factor.  Derive from gate_batch fields; fall back to
    # gate_batch.fdr_q when c(n) is not directly available.
    fdr_adjusted_threshold: float | None = None
    if gate_batch is not None:
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
            "gate_reason": (
                gr.verdict.decision.replace("_", " ").title()
                if gr and gr.verdict.vetoes_passed
                else ("veto failed" if gr else None)
            ),
            "validation_days": gr.validation_days if gr else None,
            "oos_alpha": gr.oos_alpha if gr else None,
            "winner_p_adj": gr.winner_p_adj if gr else None,
            # AC-9: statistical-power flag, threshold in app.py only — never
            # duplicated client-side.
            "low_power": _low_power(getattr(gr, "validation_days", None)) if gr else False,
            # AC-7: pbo_veto / below_spy_alpha / oos_inferior_to_incumbent /
            # fdr_not_winner / None — the granular cause, distinct from the
            # coarse gate_reason title above (which collapses all veto
            # failures into "veto failed"). None on a genuine survivor.
            "rejection_reason": getattr(gr, "rejection_reason", None) if gr else None,
            # FDR metadata for audit trail (AC-3.2)
            "n_candidates": gate_batch.n_candidates if gate_batch else None,
            "fdr_q": gate_batch.fdr_q if gate_batch else None,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            # Caveats (mandatory for survivors, AC-3.3); N=1-honest per AC-6.
            "caveats": _n1_honest_caveats(p.caveats),
            # Apply guidance — plain text, no button (AC-X1 / AC-3.4)
            "apply_guidance": p.apply_guidance,
            "backtest_error": _translate_backtest_error(p.backtest_error),
            "data_warnings": p.data_warnings,
        }

    # AC-9 (r1-review Checkpoint-3 BLOCK finding): low_power's BOOLEAN was
    # already computed above, but the caveat TEXT was never appended to any
    # survivor's caveats list on this route. Additive on survivors_detail
    # only — mirrors the SB route's identical post-processing loop
    # (app.py's SB _gate_result_to_dict caller) — a rejected candidate's
    # fold length is moot, it didn't clear the gate either way.
    _survivors_detail = [_proposal_to_dict(p) for p in run_result.survivors]
    for _survivor in _survivors_detail:
        if _survivor["low_power"]:
            _survivor["caveats"] = [*_survivor["caveats"], _LOW_POWER_CAVEAT]

    # R2-2 (AC-5): run-level provenance — read straight off run_result.provenance
    # (the engine's real 4-key contract, reflecting what build_reasoning_context
    # actually found for this symphony), defensive getattr+isinstance guard —
    # identical MagicMock-safety idiom to the shipped SB route
    # (app.py's ai_advisor_strategy_builder_run, provenance = getattr(...)).
    # getattr's default alone is not enough against a bare Mock stand-in (it
    # auto-vivifies ANY attribute access into a child Mock) — the isinstance
    # check is the only reliable guard. Falls back to the route-minted default
    # instead of None (AC-5: never None — stricter than SB, which falls back
    # to None on this same guard).
    provenance = getattr(run_result, "provenance", None)
    if not isinstance(provenance, dict):
        provenance = _default_provenance

    try:
        return jsonify(
            {
                # Run-level fields (AC-3.1: zero survivors is valid, not silent)
                "message": run_result.message,
                "survivors": len(run_result.survivors),
                "no_api_key": run_result.no_api_key,
                # Proposal detail for rendering
                "survivors_detail": _survivors_detail,
                "rejected_detail": [_proposal_to_dict(p) for p in run_result.rejected_candidates],
                # Gate verdict shortcut (for tests that check flat gate_decision key)
                "gate_decision": gate_result.verdict.decision if gate_result else None,
                "gate_result": {
                    "decision": gate_result.verdict.decision,
                    "validation_days": gate_result.validation_days,
                    "oos_alpha": gate_result.oos_alpha,
                    "winner_p_adj": gate_result.winner_p_adj,
                    # AC-9: statistical-power flag, threshold in app.py only —
                    # never duplicated client-side.
                    "low_power": _low_power(getattr(gate_result, "validation_days", None)),
                    # AC-7: granular rejection cause — see the identical field
                    # on _proposal_to_dict above for the per-candidate version
                    # (this is the run-level/primary-proposal shortcut).
                    "rejection_reason": getattr(gate_result, "rejection_reason", None),
                }
                if gate_result
                else None,
                # FDR metadata at run level (AC-3.2)
                "n_candidates": gate_batch.n_candidates if gate_batch else None,
                "fdr_q": gate_batch.fdr_q if gate_batch else None,
                "fdr_adjusted_threshold": fdr_adjusted_threshold,
                # Caveats + guidance from the primary proposal (operator-initiated = single candidate)  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
                # N=1-honest per AC-6.
                "caveats": _n1_honest_caveats(proposal.caveats if proposal else None),
                "apply_guidance": proposal.apply_guidance if proposal else "",
                "backtest_error": _translate_backtest_error(proposal.backtest_error)
                if proposal
                else None,
                "objective_rationale": proposal.objective_rationale if proposal else "",
                "provenance": provenance,
            }
        ), 200
    except Exception as _je:
        _daemon_log.error(
            "ai_advisor_logic_changes_evaluate response serialization failed: %s",
            _je,
            exc_info=True,
        )
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(_je).__name__, "provenance": _default_provenance}), 200


@app.route("/ai-advisor/strategy-builder", methods=["GET"])
def ai_advisor_strategy_builder():
    """Redirect to the unified /ai-advisor page (SPA-port fold-in).

    The Strategy Builder content is now the 6th in-place tab panel on /ai-advisor.
    Old bookmarks and links redirect cleanly rather than 404ing — same pattern as
    Correlations, Asset-Swaps, Logic-Changes, and Chat GET sub-routes.

    POST /ai-advisor/strategy-builder/run remains the action endpoint (unchanged).
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


@app.route("/ai-advisor/strategy-builder/run", methods=["POST"])
def ai_advisor_strategy_builder_run():
    """Operator-initiated strategy-builder proposal endpoint (AC-2, AC-5, AC-X1).

    Accepts JSON: { objective, universe, symphony_id? }.
    Calls propose_strategies from advisors.strategy_builder_engine, gates
    candidates via the full FDR batch, and returns JSON with survivor/rejected
    detail plus FDR metadata for the operator audit trail.

    Never runs a live trade; never calls Composer write endpoints (AC-X1).
    CSRF is enforced by _csrf_before_request @before_request hook — not called here.
    NOT added to _SETTINGS_WRITE_ALLOWLIST (this is not a settings write).
    No LIVE_EXECUTION interaction anywhere.

    No-key check is intentionally delegated to propose_strategies() — it already
    returns ProposalRun(error=...) when no key is configured, and the route
    surfaces that as the JSON error field.  An early _has_composer_key() guard
    here would be intercepted before the mock in unit tests, breaking C-10.
    """
    # Lazy imports keep strategy_builder_engine off the live 1-minute execution path (AC-X2).
    from advisors.build_plan_generator import load_atlas_candidates  # noqa: PLC0415
    from advisors.strategy_builder_engine import (  # noqa: PLC0415
        Objective,
        ScreenConfig,
        propose_strategies,
    )

    body = request.get_json(silent=True) or {}
    objective_str = str(body.get("objective", "diversify")).strip()
    universe_raw = body.get("universe", [])
    if isinstance(universe_raw, str):
        # Accept comma-separated string as well as a list.
        universe = [t.strip().upper() for t in universe_raw.split(",") if t.strip()]
    else:
        universe = [str(t).strip().upper() for t in universe_raw if str(t).strip()]
    symphony_id = str(body.get("symphony_id", "")).strip()

    # Parse objective string to enum; default to diversify on unknown values.
    try:
        objective = Objective(objective_str)
    except ValueError:
        objective = Objective.diversify

    # Load community candidates via the objective-matched admission path (AC-12/AC-13).
    # load_atlas_candidates is D-1 (never-raises) and bill-protected (force_refresh=False
    # inside). On any Atlas failure it returns [] so the template-only run proceeds (AC-4).
    community_candidates: list = []
    try:
        community_candidates = load_atlas_candidates(objective)
    except Exception as exc:
        _daemon_log.warning("community-strats load skipped: %s", type(exc).__name__)
        community_candidates = []

    # AC-12: no live-portfolio return series is available at route time (this
    # route is not necessarily symphony-scoped — symphony_id is optional).
    # Rather than silently skipping the drawdown/Pearson screens (sbe.py:746-749),
    # the response carries an explicit screens_skipped indicator below so the
    # operator knows those screens did not run this batch.
    _live_returns: list[float] = []

    # R2-1 (AC-1/AC-2/AC-8): a symphony-scoped run gets the operator's real
    # tree + live stats + lens blocks injected into the generation prompt via
    # ai_advisor.build_reasoning_context. The from-scratch path (no
    # symphony_id) never calls it at all — zero extra I/O, byte-preserving
    # today's generation prompt (AC-8). composer_symphony_id resolution
    # mirrors the existing NAME->hash bot_state lookup used by the asset-swap
    # route (app.py:4373-4387) — this route's symphony_id is the canonical
    # normalized-name id (analytics.list_available_symphonies), not the raw
    # Composer hash (project's AI Advisor Composer hash rule).
    reasoning_context: str | None = None
    reasoning_manifest: dict | None = None
    if symphony_id:
        _composer_hash = None
        _bot_state = database.load_state()
        for _sym_key, _sym_data in _bot_state.items():
            if not isinstance(_sym_data, dict) or "name" not in _sym_data:
                continue
            if database.normalize_name(_sym_data["name"]) == database.normalize_name(symphony_id):
                _composer_hash = _sym_key
                break
        reasoning_context, reasoning_manifest = ai_advisor.build_reasoning_context(
            symphony_id, objective, composer_symphony_id=_composer_hash
        )

    try:
        run = propose_strategies(
            objective=objective,
            universe=universe,
            screen_config=ScreenConfig(),
            live_returns=_live_returns,
            symphony_id=symphony_id,
            community_candidates=community_candidates,
            reasoning_context=reasoning_context,
            reasoning_manifest=reasoning_manifest,
            # F-030: attribute every advisory-DB write from this call to this
            # on-demand HTTP route (register finding — direct engine calls
            # bypass Flask/HTTP logging and are otherwise unattributable).
            invocation_source="http-route:/ai-advisor/strategy-builder/run",
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_strategy_builder_run failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(exc).__name__}), 200

    # Surface top-level engine error (e.g. no API key, unexpected exception).
    # AC-23/security: run.error is set by propose_strategies via str(exc), which can
    # carry API keys or internal paths.  Surface only a safe token — same contract as
    # the route's own outer except (type(exc).__name__ at app.py:3829).
    if run.error:
        _daemon_log.warning("strategy_builder_engine returned error: %s", run.error)
        # AC-11: prefer the engine's sanitized error_category (a type(exc).__name__
        # token, D-1/AC-23-safe) when the field exists; getattr defaults to None so
        # this route never breaks while the field lands on ProposalRun.  NEVER
        # surface run.error itself (raw str(exc) — may carry credentials, internal
        # hostnames, or paths) — same contract as the static token below.
        _error_category = getattr(run, "error_category", None)
        return jsonify(
            {
                "survivors": [],
                "rejected": [],
                "n_candidates": 0,
                "fdr_adjusted_threshold": None,
                "error": "strategy-builder-error",
                "error_category": _error_category,
            }
        ), 200

    # Build FDR adjusted threshold — Yekutieli c(n) correction (matches AC-3.2).
    gate_batch = run.gated_batch
    fdr_adjusted_threshold: float | None = None
    if gate_batch is not None:
        n = gate_batch.n_candidates or 1
        # Yekutieli c(n) = sum(1/k for k in 1..n) — same formula as autotuner._c_yekutieli.
        c_n = sum(1.0 / k for k in range(1, n + 1))
        fdr_adjusted_threshold = gate_batch.fdr_q / c_n if c_n > 0 else gate_batch.fdr_q

    # Build survivor list from screened_survivors.
    def _gate_result_to_dict(gr) -> dict:
        """Serialise a CandidateGateResult to a JSON-friendly dict."""
        # Look up matching CandidateInfo for metrics and template provenance.
        info = next((i for i in run.candidates if i.candidate_id == gr.candidate_id), None)
        return {
            "candidate_id": gr.candidate_id,
            "template_id": info.template_id if info else None,
            "gate_decision": gr.verdict.decision if gr.verdict else None,
            "winner_p_adj": gr.winner_p_adj,
            "caveats": list(gr.caveats) if gr.caveats else [],
            "metrics": info.metrics if info else {},
            "params": info.params if info else {},
            "n_candidates": gate_batch.n_candidates if gate_batch else None,
            "fdr_q": gate_batch.fdr_q if gate_batch else None,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            # AC-9: statistical-power flag, threshold in app.py only — never
            # duplicated client-side. getattr (not gr.validation_days) since
            # some pre-existing tests construct `gr` as a bare
            # types.SimpleNamespace without this field — real
            # CandidateGateResult instances always carry it.
            "low_power": _low_power(getattr(gr, "validation_days", None)),
            # AC-7 (r1-review Checkpoint-3 BLOCK finding, continuation): pbo_veto /
            # below_spy_alpha / oos_inferior_to_incumbent / fdr_not_winner / None —
            # already computed on every CandidateGateResult (backtest_gate_engine.py)
            # for SB's gated results, but never copied into this route's JSON, unlike
            # the asset-swap (app.py's ai_advisor_asset_swaps_evaluate) and
            # logic-change (ai_advisor_logic_changes_evaluate) routes, which already
            # do this. None on a genuine survivor — never fabricated.
            "rejection_reason": getattr(gr, "rejection_reason", None),
        }

    survivors_list = [_gate_result_to_dict(gr) for gr in run.screened_survivors]
    # AC-9: the power caveat is additive on SURVIVOR cards only (a rejected
    # candidate's fold length is moot — it didn't clear the gate either way).
    for _survivor in survivors_list:
        if _survivor["low_power"]:
            _survivor["caveats"] = [*_survivor["caveats"], _LOW_POWER_CAVEAT]

    # Derive rejected from gated_batch.results minus screened_survivors (AC-3.2).
    # ProposalRun has no rejected_candidates attribute — compute from gate batch.
    screened_ids = {gr.candidate_id for gr in run.screened_survivors}
    rejected_list = [
        _gate_result_to_dict(gr)
        for gr in run.gated_batch.results
        if gr.candidate_id not in screened_ids
    ]

    # AC-11 (F5, Gap E): run-level built-new/Atlas provenance rollup, derived
    # from the real candidate mix — never hardcoded. Without this, a run
    # where all built-new (Opus) branches failed and only Atlas community
    # candidates populate the result renders as an ordinary success; the
    # operator cannot tell "Opus produced nothing" from "Opus produced
    # everything you see" (silent-degradation risk, audit F5).
    built_new_count = sum(1 for c in run.candidates if c.template_id == "built-new")
    atlas_count = sum(1 for c in run.candidates if c.template_id == "atlas-suggested")
    mode_notice = (
        f"Opus generation produced 0 plans (degraded) — this run surfaces "
        f"{atlas_count} Atlas community candidate(s) only."
        if built_new_count == 0
        else None
    )

    # AC-4/AC-5 (DEGRADE-FIX, advisor-outage-degrade.md): honest run-level
    # signal when candidates were compiled but NOT tradeability-checked
    # because Composer's /backtest was unreachable (infra/transport failure —
    # see plan_tree_compiler's infra-vs-400 classifier — NOT a genuine gate
    # rejection). Read straight off `run` (same pattern as run.error/
    # run.error_category above) rather than recomputed here — run.candidates
    # excludes exactly this population (Step 2's own per-candidate backtest
    # call hits the same outage and strips the candidate before this route
    # ever sees it), so a route-side recount would silently read 0 in the
    # outage case this cycle exists to catch (see strategy_builder_engine.py's
    # ProposalRun.backtest_unavailable rollup for the verified fix).
    backtest_unavailable = bool(getattr(run, "backtest_unavailable", False))
    backtest_unavailable_count = getattr(run, "backtest_unavailable_count", 0)
    backtest_unavailable_notice = (
        f"{backtest_unavailable_count} candidate(s) could not be "
        f"tradeability-checked — Composer backtest unavailable"
        if backtest_unavailable
        else None
    )

    # AC-4/AC-6 (R2-1): run-level provenance — generation model, mode,
    # injected-evidence manifest, and run-id, surfaced verbatim from
    # run.provenance (already the exact 4-key contract on the engine side —
    # no route-side merge). Read via getattr, not direct attribute access,
    # same defensive pattern as backtest_unavailable_count above — a bare
    # MagicMock() ProposalRun stand-in (several pre-existing test fixtures)
    # would otherwise auto-vivify a non-None child Mock and break jsonify().
    # getattr's default alone is NOT enough here: a MagicMock auto-vivifies
    # ANY attribute access into a new child Mock, so the "default" branch of
    # getattr never fires for a bare mock missing .provenance — an isinstance
    # check is the only reliable guard (never fabricate a dict-shaped value
    # out of a Mock; honest None instead).
    provenance = getattr(run, "provenance", None)
    if not isinstance(provenance, dict):
        provenance = None

    return jsonify(
        {
            "survivors": survivors_list,
            "rejected": rejected_list,
            "n_candidates": gate_batch.n_candidates if gate_batch else 0,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            "error": None,
            "built_new_count": built_new_count,
            "atlas_count": atlas_count,
            "mode_notice": mode_notice,
            # AC-12: honest indicator when live_returns is empty — the drawdown/
            # Pearson screens (sbe.py:746-749) do not run without it.
            "screens_skipped": not bool(_live_returns),
            "screens_skipped_reason": (
                "no live returns at route time" if not _live_returns else None
            ),
            "backtest_unavailable": backtest_unavailable,
            "backtest_unavailable_count": backtest_unavailable_count,
            "backtest_unavailable_notice": backtest_unavailable_notice,
            "provenance": provenance,
        }
    ), 200


@app.route("/ai-advisor/frontrunner-builder", methods=["GET"])
def ai_advisor_frontrunner_builder():
    """Redirect to the unified /ai-advisor page (SPA model — no standalone page).

    Mirrors the existing redirect-stub pattern for every other Advisor
    sub-route (correlations/asset-swaps/logic-changes/chat/strategy-builder).
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


@app.route("/ai-advisor/frontrunner-builder/run", methods=["POST"])
def ai_advisor_frontrunner_builder_run():
    """Operator-initiated Frontrunner Builder run (AC-1 on-demand, AC-8).

    Dispatches advisors.frontrunner_builder.run_frontrunner_build to a
    dedicated background executor (_FRONTRUNNER_BUILD_EXECUTOR) and returns
    202 immediately. run_frontrunner_build iterates every live symphony (up
    to MAX_CASCADES_PER_SYMPHONY_RUN cascades each) with rate-limited Fable +
    Composer calls — genuinely multi-minute — and must never block a Flask
    request thread (dashboard Prime Directive: never a live-trade-action
    surface, never blocks/slows the minute-by-minute execution loop). Results
    persist straight to frontrunner_proposals (SQLite); the operator "polls"
    by reloading /ai-advisor to see newly-queued server-rendered proposal
    cards — there is no synchronous result body and no new JSON polling
    endpoint.

    Accepts JSON: { symphony_ids?: [str] }. Omitted/empty -> full live
    roster (run_frontrunner_build's own default).

    Fails fast (200 + error, never submits to the executor) when
    ANTHROPIC_API_KEY is absent — the build needs it for Fable candidate
    generation; a doomed background job should never be queued.

    The submitted work is a log-and-swallow closure wrapping
    run_frontrunner_build (RULING, team-lead, 2026-07-11 — mirrors
    _dismiss_async above): run_frontrunner_build is documented D-1/
    never-raises, but an unawaited Future silently drops any exception that
    somehow escapes that contract — the wrapper makes a D-1 violation
    observable in the logs (defense-in-depth) instead of silently lost.

    CSRF is enforced by _csrf_before_request @before_request hook — not
    called here. NOT added to _SETTINGS_WRITE_ALLOWLIST (not a settings
    write). No LIVE_EXECUTION interaction anywhere.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "advisor unavailable: ANTHROPIC_API_KEY not configured"}), 200

    body = request.get_json(silent=True) or {}
    symphony_ids_raw = body.get("symphony_ids")
    symphony_ids = (
        [str(s).strip() for s in symphony_ids_raw if str(s).strip()] if symphony_ids_raw else None
    )

    # CC-2 lazy import — keeps advisors.frontrunner_builder off app.py's
    # module-scope import graph / the live 1-minute execution path.
    from advisors.frontrunner_builder import run_frontrunner_build  # noqa: PLC0415

    def _run_frontrunner_build_background(*, symphony_ids=None):
        try:
            run_frontrunner_build(symphony_ids=symphony_ids)
        except Exception as exc:
            # Log-and-swallow (mirrors _dismiss_async, app.py:2831): this is
            # a defense-in-depth net for a D-1 contract violation, not a
            # normal path — server-side log only, never surfaced to a
            # response (the 202 was already sent before this can fire).
            _daemon_log.error(
                "ai_advisor_frontrunner_builder_run: background run failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    try:
        _FRONTRUNNER_BUILD_EXECUTOR.submit(
            _run_frontrunner_build_background, symphony_ids=symphony_ids
        )
    except Exception as exc:
        _daemon_log.error(
            "ai_advisor_frontrunner_builder_run: dispatch failed: %s", exc, exc_info=True
        )
        # D-1 security contract: do NOT echo str(exc) — same rationale as
        # ai_advisor_strategy_builder_run's own outer except above.
        return jsonify({"error": type(exc).__name__}), 200

    return jsonify({"status": "started"}), 202


@app.route("/ai-advisor/proposal/approve", methods=["POST"])
def ai_advisor_proposal_approve():
    """Generic approval route for frontrunner_proposals rows (AC-9/AC-10).

    Serves BOTH proposal sources — 'frontrunner_builder' and
    'strategy_builder_retrofit' — since both land in the SAME
    frontrunner_proposals table (migration 033) and both flow through the
    identical advisors.frontrunner_builder.approve_frontrunner_proposal,
    which is itself source-agnostic (keyed purely by row id). RULED
    (team-lead, 2026-07-11): a single opaque proposal_id, no source
    disambiguation param.

    THIS IS THE ONLY ROUTE IN THE APP THAT CAN REACH
    composer_draft_client.save_symphony — exclusively via
    approve_frontrunner_proposal, never called directly here. Approval
    creates a NEW UNDEPLOYED Composer symphony (verify_undeployed enforced
    inside the called function) — never a trade, never a deploy/invest call.

    Accepts JSON: { proposal_id: <int> }. Bounded (1-2 Composer calls) — safe
    to run synchronously in-request, unlike /run.

    CSRF is enforced by _csrf_before_request @before_request hook — not
    called here. NOT added to _SETTINGS_WRITE_ALLOWLIST. No LIVE_EXECUTION
    interaction.
    """
    body = request.get_json(silent=True) or {}
    try:
        proposal_id = int(body.get("proposal_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "invalid proposal_id"}), 200

    # CC-2 lazy import — keeps advisors.frontrunner_builder off app.py's
    # module-scope import graph / the live 1-minute execution path.
    from advisors.frontrunner_builder import approve_frontrunner_proposal  # noqa: PLC0415

    try:
        result = approve_frontrunner_proposal(proposal_id)
    except Exception as exc:
        _daemon_log.error("ai_advisor_proposal_approve failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — may carry Composer
        # credentials or internal paths.
        return jsonify({"error": type(exc).__name__}), 200

    return jsonify(
        {"success": result.success, "symphony_id": result.symphony_id, "error": result.error}
    ), 200


@app.route("/ai-advisor/proposal/reject", methods=["POST"])
def ai_advisor_proposal_reject():
    """Generic rejection route for frontrunner_proposals rows (AC-9/AC-10).

    Status-only DB write — never touches composer_draft_client (same
    shared-table rationale as ai_advisor_proposal_approve above).

    Accepts JSON: { proposal_id: <int> }.

    CSRF is enforced by _csrf_before_request @before_request hook — not
    called here. NOT added to _SETTINGS_WRITE_ALLOWLIST. No LIVE_EXECUTION
    interaction.
    """
    body = request.get_json(silent=True) or {}
    try:
        proposal_id = int(body.get("proposal_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "invalid proposal_id"}), 200

    try:
        updated = database.update_frontrunner_proposal_status(
            proposal_id, approval_status="rejected"
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_proposal_reject failed: %s", exc, exc_info=True)
        return jsonify({"error": type(exc).__name__}), 200

    return jsonify({"success": bool(updated)}), 200


def _dispatch_retirement_decision(approval_status: str):
    """Shared body for the two retirement approve/reject routes (Cycle 2b,
    AC-5) -- a status-ONLY write via database.upsert_retirement_decision.

    Deliberately NOT the frontrunner /ai-advisor/proposal/approve route
    (reaches composer_draft_client.save_symphony) -- this reaches no
    Composer/exec/LIVE_EXECUTION/trade primitive of any kind, and never
    calls advisors.retirement_checklist.build_checklist (the checklist is
    assembled at render time in ai_advisor_tab(), not here).

    candidate_id is validated as a non-empty str under a bounded length (300
    chars, matching Composer-hash-scale ids) -- deliberately NOT gated
    against the current latest recommendation batch (team-lead-approved
    deviation from the plan's "where practical" wording): the nightly 03:45
    tick can rebuild between an operator's page load and their click, and
    this is a status write on an advisory record, not a money-moving action
    -- an orphaned decision row is harmless (plan Edge Cases).

    CSRF via the global _csrf_before_request hook, auth via the global
    _auth_before_request hook -- neither is called here. Not added to
    _SETTINGS_WRITE_ALLOWLIST. D-1: any accessor exception returns
    type(exc).__name__ only, never str(exc).
    """
    body = request.get_json(silent=True) or {}
    candidate_id = body.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 300:
        return jsonify(
            {"success": False, "approval_status": None, "error": "invalid candidate_id"}
        ), 200

    try:
        database.upsert_retirement_decision(candidate_id, approval_status=approval_status)
    except Exception as exc:
        _daemon_log.error(
            "_dispatch_retirement_decision(%s) failed: %s", approval_status, exc, exc_info=True
        )
        # D-1 security contract: do NOT echo str(exc) — may carry a DB path.
        return jsonify(
            {"success": False, "approval_status": None, "error": type(exc).__name__}
        ), 200

    return jsonify({"success": True, "approval_status": approval_status, "error": None}), 200


@app.route("/ai-advisor/retirement/approve", methods=["POST"])
def ai_advisor_retirement_approve():
    """Approve a retirement recommendation (Cycle 2b, AC-5).

    Accepts JSON: { candidate_id: <str> }. Status-only DB write via
    database.upsert_retirement_decision(candidate_id, approval_status="approved")
    -- reaches no Composer/exec/trade primitive and never reads or writes
    the live-execution env flag. Never calls advisors.retirement_checklist.
    build_checklist (assembled at render time) or advisors.retirement_
    explainer.explain_recommendation (producer-time only). See
    _dispatch_retirement_decision for the shared contract.
    """
    return _dispatch_retirement_decision("approved")


@app.route("/ai-advisor/retirement/reject", methods=["POST"])
def ai_advisor_retirement_reject():
    """Reject a retirement recommendation (Cycle 2b, AC-5).

    Accepts JSON: { candidate_id: <str> }. Status-only DB write via
    database.upsert_retirement_decision(candidate_id, approval_status="rejected")
    -- reaches no Composer/exec/trade primitive and never reads or writes
    the live-execution env flag. See _dispatch_retirement_decision for the
    shared contract.
    """
    return _dispatch_retirement_decision("rejected")


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
        "oos_reason": "OOS Sharpe degraded -0.11 in 2 of 4 windows; insufficient walk-forward support.",  # noqa: E501  # un-wrappable long line
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
        # Resolve Composer hash ID → normalized symphony name so that
        # get_latest_autotune_run (keyed by name) returns the correct row.
        # Mirrors the hash→name resolution pattern at app.py:2497-2507.
        _bot_state = database.load_state()
        resolved_id = symphony_id  # fallback: pass as-is if no match found
        # F-023: resolved_hash mirrors resolved_id but tracks the HASH side of
        # the same match (_sym_key), not the name side — closes the gap where
        # composer_symphony_id was passed through unresolved (raw caller input)
        # regardless of whether the caller supplied a hash or a name.
        resolved_hash = symphony_id  # fallback: pass as-is if no match found
        for _sym_key, _sym_data in _bot_state.items():
            if not isinstance(_sym_data, dict) or "name" not in _sym_data:
                continue
            _norm_name = database.normalize_name(_sym_data["name"])
            if database.normalize_name(_sym_key) == database.normalize_name(
                symphony_id
            ) or _norm_name == database.normalize_name(symphony_id):
                resolved_id = _norm_name
                resolved_hash = _sym_key
                break
        # Fetch the autotune run here (through app.py's database reference) so
        # the per-symphony assessment can be built from real DB data — and so
        # route-level tests can mock this call via patch.object(app_module, "database").
        autotune_run = database.get_latest_autotune_run(resolved_id)
        context = ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id=resolved_id,
            # F-023: resolve to the matching Composer HASH regardless of
            # whether the caller supplied a hash or a name — get_condensed_logic
            # calls the Composer /score API, which requires a hash; passing a
            # name through unresolved silently 400s and empties that context.
            composer_symphony_id=resolved_hash,
            # Pass the pre-fetched autotune run so assemble_advisor_context
            # skips its own database.get_latest_autotune_run call — avoids a
            # second DB round-trip and ensures the route-level DB mock covers
            # the context assembly (ai_advisor.database is a separate import).
            autotune_run=autotune_run,
        )
        suggestions_response, error_msg = ai_advisor.request_suggestions(context)
        if error_msg is not None:
            return jsonify({"error": error_msg}), 200
        suggestions = []
        for s in suggestions_response.suggestions:
            s_dict = s.model_dump()
            s_dict["four_gates_verdict"] = _compute_suggestion_gates(s, resolved_id)
            s_dict["impact"] = _enrich_suggestion_impact(s)
            suggestions.append(s_dict)
        # AC1: include per-symphony assessment alongside suggestions so the UI
        # can show real context when the suggestions list is empty (the common
        # case for symphonies with no validated edge). Build assessment from the
        # autotune_run fetched above (through this module's database reference,
        # so route-level tests can mock it) by constructing an optuna_evidence
        # context slice and delegating to build_assessment_from_context.
        _evidence_context = {
            "optuna_evidence": ai_advisor._build_optuna_section(autotune_run),
        }
        assessment = ai_advisor.build_assessment_from_context(_evidence_context)
        return jsonify(
            {
                "suggestions": suggestions,
                "assessment": assessment,
                # Lens-cache staleness stamp — AC-3.  The timestamp is server-generated
                # ISO UTC; never user-supplied.  Escape with | e where rendered in a
                # template.  None when the cache has never been populated (cold-start).
                "lens_data_as_of": context.get("lens_data_as_of"),
                "lens_data_stale": context.get("lens_data_stale"),
            }
        )
    except Exception as _exc:
        _daemon_log.error("ai_advisor_suggest failed: %s", _exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys, internal paths, or secrets.  exc_info=True above puts full detail
        # server-side.  Surface only the error class name so the operator can identify
        # the failure type without leaking sensitive content to the browser.
        return jsonify({"error": type(_exc).__name__}), 200


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
        model_id=ai_advisor.resolve_advisor_model(),
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
        model_id=ai_advisor.resolve_advisor_model(),
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
    """Redirect to the unified /ai-advisor page (in-place tabs migration).

    Chat is now an always-in-DOM slide panel on /ai-advisor, not a separate page.
    """
    return redirect(url_for("ai_advisor_tab"), code=302)


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
        return jsonify(
            {"error": f"message exceeds maximum length of {CHAT_MAX_MESSAGE_CHARS} characters"}
        ), 400

    # --- AC-2c: artifact size cap ---
    # Measure the artifact's JSON footprint before passing it to the LLM.
    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    artifact_json_size = len(_json.dumps(artifact))
    if artifact_json_size > CHAT_MAX_ARTIFACT_BYTES:
        return jsonify(
            {"error": f"artifact exceeds maximum size of {CHAT_MAX_ARTIFACT_BYTES} bytes"}
        ), 400

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
# DIVERGENCE_EXPLAINER is intentionally excluded: its CVaR-divergence core is
# permanently rejected (project memory: project_cvar_divergence_validation_wall);
# with the feature flag off it contributes only NOT_APPLICABLE rows that read as
# a dead producer.  DE-retire decision: Cycle C, 2026-06-08.
_ADVISOR_ROLES = [
    "OVERFITTING_CONSCIENCE",
    "SPEC_CRITIC",
    "NARRATOR",  # DEFERRED per Sprint 3 scope — producer not yet shipped
    "MARKET_PRISM",  # Cycle-1 scaffold — always-on market overview (GATE-1-AC §8)
    "ADD_CANDIDATE",  # Cycle-1 scaffold — backtest-agnostic add-candidate advisory (GATE-1-AC §3)
    "ASSET_SWAP",  # AC-A2 — weekly auto asset-swap suggestions (advisors/asset_swap_engine.py)
    "LOGIC_CHANGE",  # AC-A2 — weekly auto logic-change suggestions (advisors/logic_change_engine.py)
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

    # AC-14 (F8, Gap; comment corrected DE-AUDIT-BL7-001): the symphony_id
    # branch above reads get_advisor_observations_for_symphony directly with
    # no role filter, so a DIVERGENCE_EXPLAINER feature-off NOT_APPLICABLE row
    # could leak through unlabeled. As of AC-14
    # (advisors/divergence_explainer.py's run_divergence_explainer),
    # DIVERGENCE_EXPLAINER writes NOTHING (returns None) per autotune run
    # while SECOND_WINDOW_CVAR_ENABLED is off — it no longer produces new
    # NOT_APPLICABLE rows at all. This filter is legacy-row DEFENSE, not
    # ongoing-write suppression: it exists solely to keep the 22 pre-AC-14
    # NOT_APPLICABLE rows already in the DB from leaking through this
    # unfiltered symphony_id path (see _ADVISOR_ROLES's comment above — the
    # no-symphony_id branch above already can't leak them, since
    # _ADVISOR_ROLES excludes DIVERGENCE_EXPLAINER; this filter is a no-op
    # there and only closes the gap on the symphony_id path). Same predicate
    # as the Overview panel's own suppression (ai_advisor_tab(), feature-off
    # stub filter).
    rows = [
        row
        for row in rows
        if not (
            row.get("verdict") == "NOT_APPLICABLE"
            and isinstance(row.get("raw_response"), dict)
            and row["raw_response"].get("feature_flag") == "off"
        )
    ]

    rows = rows[:_ADVISOR_OBSERVATIONS_PAGE_LIMIT]
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
        row["frozen_eval_verdict"] = (
            "passed" if frozen_sharpe >= _FROZEN_EVAL_SHARPE_THRESHOLD else "failed"
        )
    else:
        row["frozen_eval_verdict"] = None
    return row


@app.route("/api/autotune-runs", methods=["GET"])
def api_autotune_runs():
    """Return recent autotune run rows including all three Sharpe metrics.

    BL-8 (DE-AUDIT-BL8-001, audit #118 T3): each row additively gains a
    `never_adopted_streak` field — the "silent-never-tuned" signal
    (analytics.compute_never_adopted_streak) computed by grouping the SAME
    already-fetched rows by symphony_id (zero extra DB round-trip). This
    communicates the ACCUMULATED pattern across a symphony's runs, distinct
    from the row's own single-run baseline_decision. The response stays a
    bare JSON ARRAY (AC-3, existing consumer contract — tests/reporting/
    test_dsr_surfacing.py's TestAutotuneRunsApiRoute pins `isinstance(data,
    list)`); the streak is computed from the RAW (pre-normalization) rows so
    its "Adopted AI" comparison always matches autotuner.py's real persisted
    literal, independent of _normalize_autotune_row's short-token remap.
    """
    _ro_conn = database.get_ro_connection()
    try:
        rows = database.get_all_autotune_runs(limit=50)
        by_symphony: dict[str, list[dict]] = {}
        for r in rows:
            by_symphony.setdefault(r["symphony_id"], []).append(r)
        never_adopted_streaks = {
            sym_id: analytics.compute_never_adopted_streak(sym_rows)
            for sym_id, sym_rows in by_symphony.items()
        }
        normalized = []
        for r in rows:
            n = _normalize_autotune_row(r)
            n["never_adopted_streak"] = never_adopted_streaks[r["symphony_id"]]
            normalized.append(n)
        return jsonify(normalized)
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

    # Seed bot_state with baseline symphony entries before the scheduler starts.
    # Lazy import avoids any circular-import risk at module level; the call is a
    # no-op when entries already exist (idempotent — AC-2) and never raises (AC-4).
    from alpha_bot_execution import ensure_bot_state_seeded  # noqa: PLC0415

    ensure_bot_state_seeded()

    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"\nStarting Alpha Bot Control Center at http://localhost:{port}\n")

    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=port, debug=False, use_reloader=False)
