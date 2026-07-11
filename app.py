"""Flask application for Planet Stopper Control Center with Account-Level settings."""

import atexit
import concurrent.futures
import hmac
import io
import logging
import os
import queue
import secrets
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
from dotenv import dotenv_values, load_dotenv, set_key
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

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
_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 10
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
    global _account_totals_last_good, _account_totals_last_success_at
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


def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    schedule.every().minute.at(":00").do(_refresh_account_totals)
    schedule.every().day.at("02:00").do(_run_trigger_retention)
    # Component 7+8: daily off-hours lens pipeline — Market Prism summary (CYCLE4-BRIEF.md).
    # Runs at 03:00 (off-hours) so it never overlaps the live market-hours execution path.
    schedule.every().day.at("03:00").do(_run_lens_pipeline)
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
            _s["_cr"] = _safe_analytics(
                analytics.get_symphony_cumulative_return, _sym_dict, _s, trading_day=_dash_today
            )
        if "_tc" not in _s:
            _s["_tc"] = _safe_analytics(
                analytics.get_symphony_today_change, _sym_dict, _s, trading_day=_dash_today
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


def _compute_portfolio_strip(bot_state: dict, trading_day: str | None = None) -> dict:
    """Compute portfolio_strip from bot_state using analytics helpers.

    Shared by get_api_state_dict() (Jinja render path) and get_state() (JSON
    poll path) so both paths emit identical portfolio_strip shape.  This closes
    the systemic 0.00% everywhere defect (FP-T1-01).

    Option A: the windowed hero metrics (guard_alpha + window echo) are computed via
    analytics.compute_windowed_portfolio_strip at _DEFAULT_HERO_WINDOW — the SAME path
    /api/strip/<window> uses — so the default hero matches the picker's first click. The
    account-lifetime CR (~Composer simple_return) is surfaced SEPARATELY as
    account_all_time_cr: it carries no window label and never windows.
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
                symphonies_list, bot_state, trading_day=trading_day
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
                    symphonies_list, bot_state, trading_day=trading_day
                )
                cumulative_return = analytics.get_portfolio_cumulative_return_account_basis(
                    _vw_cr, _lg_cr, account_value, _symphony_value_sum
                )
                _live_basis_stale = True
            else:
                # Tier 2 — no last-good: fall back to VW (label applied below).
                cumulative_return = analytics.get_portfolio_cumulative_return(
                    symphonies_list, bot_state, trading_day=trading_day
                )

        # D-01 / B-2 fix: use the Composer-sourced today-change (includes cash in
        # denominator) when available, and put Bot on the same account basis so that
        # guard_alpha = dry_run - if_held is zero when no guard has fired.
        # Previously: if_held = _cached_tc (account basis, cash-inclusive) but
        # dry_run = VW symphony sum (cash-excluded) — different denominators produced
        # phantom alpha even when all symphonies were bot == held.
        _cached_tc = _account_totals_cache.get("portfolio_tc")
        if _cached_tc is not None:
            _vw_tc = analytics.get_portfolio_today_change(
                symphonies_list, bot_state, trading_day=trading_day
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
                    symphonies_list, bot_state, trading_day=trading_day
                )
                today_change = analytics.get_portfolio_today_change_account_basis(
                    _vw_tc, _lg_tc, account_value, _symphony_value_sum
                )
                _live_basis_stale = True
            else:
                # Tier 2 — no last-good: fall back to VW (label applied below).
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
        _cached_mdd = _account_totals_cache.get("portfolio_mdd")
        if _cached_mdd is not None:
            max_drawdown: dict = {
                "if_held": abs(_cached_mdd),
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
                symphonies_list, bot_state, window=_DEFAULT_HERO_WINDOW
            )
            if isinstance(_default, dict):
                _ga = _default.get("guard_alpha")
                _strip["guard_alpha"] = _ga if isinstance(_ga, (int, float)) else None
                _win = _default.get("window", _DEFAULT_HERO_WINDOW)
                _strip["window"] = _win if isinstance(_win, str) else _DEFAULT_HERO_WINDOW
                # Expose the windowed VW cumulative_return so the JS poll path uses
                # VW-basis Bot/Held for the cumulative comparison row on initial load.
                # Without this field, updateComparisonRows falls back to the
                # account-basis cumulative_return (if_held ~63.95%), which mismatches
                # the windowed VW dry_run (~27.56%) and fabricates a large negative delta.
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

    portfolio_strip = _compute_portfolio_strip(bot_state)

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

                    # VW intermediates (same calls as live path).
                    _snap_vw_tc = analytics.get_portfolio_today_change(
                        _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
                    )
                    _snap_vw_cr = analytics.get_portfolio_cumulative_return(
                        _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
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
                        # No TC basis at all (no cache, no last-good): surface raw VW
                        # (honesty signalled below via the Tier-2 basis marker), matching
                        # the CR branch + the live path + the plan's documented default.
                        _snap_tc_final = _snap_vw_tc

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
                            _snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day
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
                except Exception:
                    _portfolio_strip = {
                        "today_change": None,
                        "cumulative_return": None,
                        "max_drawdown": None,
                        "account_value": _account_totals_cache.get("portfolio_value"),
                        # Same frozen-snapshot semantics as the happy path above.
                        "data_as_of": _snap_data_as_of,
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
            tc_bot = (tc.get("dry_run") if isinstance(tc, dict) else tc) or None
            tc_held = (tc.get("if_held") if isinstance(tc, dict) else None) or None
            cr_bot = (cr.get("dry_run") if isinstance(cr, dict) else cr) or None
            cr_held = (cr.get("if_held") if isinstance(cr, dict) else None) or None
            mdd_bot = (mdd.get("dry_run") if isinstance(mdd, dict) else mdd) or None
            mdd_held = (mdd.get("if_held") if isinstance(mdd, dict) else None) or None
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


@app.route("/api/hero-chart/<window>")
def get_hero_chart(window):
    """Return hist_dates/hist_bot/hist_held for the requested time window.

    window values: 30d, 60d, 90d, 125d, ytd, 1y, all
    Fetches from shadow_history with an appropriate days parameter so each
    window returns a distinct, correctly-sized slice. "all" fetches the full
    history (days=None) — the lifetime/All-Time view.
    """
    now = datetime.now(_ET)
    if window == "all":
        # All Time: fetch the full history. analytics treats days=None as "all".
        fetch_days = None
    elif window == "ytd":
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

    # Minimum trading days needed for the window to be meaningful. "all" has no
    # floor (whatever history exists is the lifetime view).
    _min_days = {"30d": 20, "60d": 40, "90d": 60, "125d": 80, "ytd": 10, "1y": 100, "all": 2}
    required = _min_days.get(window, 10)

    def _compound(daily: list[float]) -> list[float]:
        """Compound a per-day pct return series into a running cumulative-return curve."""
        running = 1.0
        out = []
        for d in daily:
            running *= 1.0 + d / 100.0
            out.append(round((running - 1.0) * 100.0, 4))
        return out

    def _trim_ytd(dates, *series):
        """For the YTD window, drop rows before Jan 1 across dates + every parallel series."""
        if window != "ytd":
            return (dates, *series)
        jan1_str = str(datetime(now.year, 1, 1).date())
        idx = 0
        while idx < len(dates) and dates[idx] < jan1_str:
            idx += 1
        return (dates[idx:], *[s[idx:] for s in series])

    try:
        # AC-4b: use the REAL (bot, held) daily-return source so the dashed "If held"
        # line is a genuine second series, not a verbatim copy of Bot. bot = guarded
        # shadow path; held = un-guarded if-held path (diverges only after a trigger).
        # Each series is compounded INDEPENDENTLY into its own cumulative curve.
        bh = analytics.get_portfolio_bot_and_held_daily_returns(days=fetch_days)
        if bh is not None:
            dates, bot_daily, held_daily = bh
            bot_series = _compound(bot_daily)
            held_series = _compound(held_daily)
            dates, bot_series, held_series = _trim_ytd(dates, bot_series, held_series)
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
    if strip.get("insufficient_history") and not strip.get("guard_alpha"):
        try:
            try:
                _bot_state_dict = database.load_state()
            except Exception:
                _bot_state_dict = {}
            _conn = database.get_connection()
            try:
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
    """
    import glob as _glob
    import json as _json  # noqa: PLC0415 — stdlib, lazy for locality

    pm_dir = analytics._POST_MORTEMS_DIR
    pattern = os.path.join(pm_dir, "post_mortem_*.json")
    files = sorted(_glob.glob(pattern))

    cumulative_saved_dollars = 0.0
    guard_event_count = 0
    dates: list[str] = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as fh:
                pm = _json.load(fh)
        except (OSError, _json.JSONDecodeError):
            _daemon_log.warning(
                "guard_alpha_summary: skipping unreadable file %s", os.path.basename(fpath)
            )
            continue

        triggers = pm.get("triggers", [])
        for t in triggers:
            cumulative_saved_dollars += float(t.get("saved_dollars", 0.0))
        guard_event_count += len(triggers)

        # Extract YYYY-MM-DD from filename post_mortem_YYYY-MM-DD.json
        basename = os.path.basename(fpath)
        date_str = basename[len("post_mortem_") : len("post_mortem_") + 10]
        if len(date_str) == 10:
            dates.append(date_str)

    if dates:
        earliest = min(dates)
        latest = max(dates)
        date_range = {"earliest": earliest, "latest": latest}
        basis_label = f"snapshot-time basis, since {earliest}"
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
            "date_range": date_range,
            "basis_label": basis_label,
            "source": source,
        }
    )


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

    # AC-3: when post-mortem files do not yet exist (day-1 droplet), todays_exits
    # will be empty in the stats dict.  Backfill from exit_triggers so the History
    # tab shows live exits on day one.
    if not stats.get("todays_exits"):
        try:
            _conn = database.get_connection()
            try:
                _rows = _conn.execute(
                    "SELECT symphony_id, ts_utc, at_return, triggered_reason "
                    "FROM exit_triggers ORDER BY ts_utc DESC LIMIT 50"
                ).fetchall()
            finally:
                _conn.close()
            if _rows:
                stats["todays_exits"] = [
                    {
                        "symphony_id": r[0],
                        "ts_utc": r[1],
                        "at_return": r[2],
                        "triggered_reason": r[3],
                    }
                    for r in _rows
                ]
                # AC-3b: keep trigger_count consistent with the backfilled todays_exits.
                stats["trigger_count"] = len(stats["todays_exits"])
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

    # AC-2: when post-mortem history is empty (day-1 droplet), fall back to
    # shadow_history for the series so the chart is non-empty from day one.
    # The insufficient_history / quantstats-min-obs guard is unchanged.
    if not dates:
        try:
            _fallback = analytics.get_portfolio_bot_and_held_daily_returns()
            if _fallback is not None:
                dates, live_returns, shadow_returns = _fallback
        except Exception:
            _daemon_log.debug("api_performance: shadow_history fallback failed", exc_info=True)

    # AC-2b: get_portfolio_bot_and_held_daily_returns() returns None when fewer than
    # 2 distinct trading days exist.  On a fresh droplet (day one), that guard fires
    # and leaves dates empty.  Fall back to the single-day seam so the chart is
    # non-empty even before the 2-day guard can pass.
    if not dates:
        try:
            _single = analytics.get_single_day_shadow_returns()
            if _single is not None:
                dates, live_returns, shadow_returns = _single
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
    history = analytics.get_history_with_cache_invalidation(base_dir=analytics._POST_MORTEMS_DIR)
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
                    sell_url = f"{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{sym.get('symphony_id') or sym.get('id')}/go-to-cash"  # noqa: E501  # un-wrappable long line
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
        _series_dict: dict[str, list[float]] = {}
        for _sym_id in _sym_ids:
            _dates, _live_rets, _shadow = analytics.compute_per_symphony_returns(_history, _sym_id)
            if _live_rets:
                _series_dict[_sym_id] = _live_rets
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
        SwapObjective,
        _has_composer_key,
        propose_operator_swap,
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
                "error": f"could not resolve name to a Composer hash: {symphony_id!r} not found in active symphonies"  # noqa: E501  # un-wrappable long line
            }
        ), 200

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
            # The engine's _persist_observation normalizes to a canonical name for
            # the advisor_observations DB key (RC-4 keying handled engine-side).
            symphony_id=composer_hash,
            score_tree=raw_value,
            incumbent_asset=from_ticker,
            candidate_asset=to_ticker,
            objective=objective,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_asset_swaps_evaluate failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(exc).__name__}), 200

    # Build response from the first proposal (single-candidate operator-initiated mode)
    # plus the run-level message and gate batch metadata (AC-2.3 / AC-2.5).
    proposal = run_result.proposals[0] if run_result.proposals else None
    gate_result = proposal.gate_result if proposal else None

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
            }
            if gate_result
            else None,
            # Caveats (mandatory for survivors — SURVIVOR_OVERFITTING_CAVEAT)
            "caveats": proposal.caveats if proposal else [],
            # Apply guidance — plain text, no button (AC-X1)
            "apply_guidance": proposal.apply_guidance if proposal else "",
            # AC-9c: translate raw nginx 413 HTML to a clean operator message.
            "backtest_error": _translate_backtest_error(proposal.backtest_error)
            if proposal
            else None,
            "data_warnings": proposal.data_warnings if proposal else [],
        }
    ), 200


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
        return jsonify({"error": f"advisor unavailable: {type(_ie).__name__}"}), 200

    if not _has_composer_key():
        return jsonify({"error": "advisor unavailable: API key not configured"}), 200

    body = request.get_json(silent=True) or {}
    symphony_id = str(body.get("symphony_id", "")).strip()
    objective_type = str(body.get("objective_type", "reduce_drawdown")).strip()
    change_description = str(body.get("change_description", "")).strip()

    if not symphony_id or not change_description:
        return jsonify({"error": "symphony_id and change_description are required"}), 200

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
                "error": f"could not resolve name to a Composer hash: {symphony_id!r} not found in active symphonies"  # noqa: E501  # un-wrappable long line
            }
        ), 200

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
            # The engine's _persist_observation normalizes to a canonical name for
            # the advisor_observations DB key (RC-4 keying handled engine-side).
            symphony_id=composer_hash,
            score_tree=raw_value,
            tweak=None,
            objective=objective,
            change_description=change_description,
        )
    except Exception as exc:
        _daemon_log.error("ai_advisor_logic_changes_evaluate failed: %s", exc, exc_info=True)
        # D-1 security contract: do NOT echo str(exc) — exception messages may contain
        # API keys or internal paths. Surface only the error class for operator triage;
        # full detail is logged server-side via exc_info=True above.
        return jsonify({"error": type(exc).__name__}), 200

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
            # FDR metadata for audit trail (AC-3.2)
            "n_candidates": gate_batch.n_candidates if gate_batch else None,
            "fdr_q": gate_batch.fdr_q if gate_batch else None,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            # Caveats (mandatory for survivors, AC-3.3)
            "caveats": p.caveats,
            # Apply guidance — plain text, no button (AC-X1 / AC-3.4)
            "apply_guidance": p.apply_guidance,
            "backtest_error": _translate_backtest_error(p.backtest_error),
            "data_warnings": p.data_warnings,
        }

    try:
        return jsonify(
            {
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
                }
                if gate_result
                else None,
                # FDR metadata at run level (AC-3.2)
                "n_candidates": gate_batch.n_candidates if gate_batch else None,
                "fdr_q": gate_batch.fdr_q if gate_batch else None,
                "fdr_adjusted_threshold": fdr_adjusted_threshold,
                # Caveats + guidance from the primary proposal (operator-initiated = single candidate)  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
                "caveats": proposal.caveats if proposal else [],
                "apply_guidance": proposal.apply_guidance if proposal else "",
                "backtest_error": _translate_backtest_error(proposal.backtest_error)
                if proposal
                else None,
                "objective_rationale": proposal.objective_rationale if proposal else "",
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
        return jsonify({"error": type(_je).__name__}), 200


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

    try:
        run = propose_strategies(
            objective=objective,
            universe=universe,
            screen_config=ScreenConfig(),
            live_returns=[],
            symphony_id=symphony_id,
            community_candidates=community_candidates,
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
        return jsonify(
            {
                "survivors": [],
                "rejected": [],
                "n_candidates": 0,
                "fdr_adjusted_threshold": None,
                "error": "strategy-builder-error",
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
        }

    survivors_list = [_gate_result_to_dict(gr) for gr in run.screened_survivors]

    # Derive rejected from gated_batch.results minus screened_survivors (AC-3.2).
    # ProposalRun has no rejected_candidates attribute — compute from gate batch.
    screened_ids = {gr.candidate_id for gr in run.screened_survivors}
    rejected_list = [
        _gate_result_to_dict(gr)
        for gr in run.gated_batch.results
        if gr.candidate_id not in screened_ids
    ]

    return jsonify(
        {
            "survivors": survivors_list,
            "rejected": rejected_list,
            "n_candidates": gate_batch.n_candidates if gate_batch else 0,
            "fdr_adjusted_threshold": fdr_adjusted_threshold,
            "error": None,
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
        for _sym_key, _sym_data in _bot_state.items():
            if not isinstance(_sym_data, dict) or "name" not in _sym_data:
                continue
            _norm_name = database.normalize_name(_sym_data["name"])
            if database.normalize_name(_sym_key) == database.normalize_name(
                symphony_id
            ) or _norm_name == database.normalize_name(symphony_id):
                resolved_id = _norm_name
                break
        # Fetch the autotune run here (through app.py's database reference) so
        # the per-symphony assessment can be built from real DB data — and so
        # route-level tests can mock this call via patch.object(app_module, "database").
        autotune_run = database.get_latest_autotune_run(resolved_id)
        context = ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id=resolved_id,
            # Pass the original Composer hash so get_condensed_logic can call
            # the Composer /score API correctly (it expects a hash, not a name).
            composer_symphony_id=symphony_id,
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
