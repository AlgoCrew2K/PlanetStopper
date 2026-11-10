"""Flask application for Planet Stopper Control Center with Account-Level settings."""

import atexit
import concurrent.futures
import contextvars
import copy
import functools
import hmac
import inspect
import ipaddress
import json
import logging
import math
import os
import queue
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import types
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import datetime as _DatetimeType  # real class, immune to a patched app.datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import dotenv as _dotenv_module
import psutil
import requests
import schedule
from dotenv import dotenv_values, load_dotenv, set_key
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import Forbidden
from werkzeug.security import check_password_hash

import ai_advisor
import analytics
import database
import env_flags
import llm_request
import market_calendar
import model_config
import timing_log
from market_calendar import get_market_state

_ET = ZoneInfo("America/New_York")

# Minimum observations before quantstats metrics are deemed statistically
# meaningful for the dashboard.  Below this floor the route surfaces
# `insufficient_history=True` so the UI can render a "not enough history yet"
# banner instead of misleadingly precise but underpowered numbers.
_PERFORMANCE_MIN_HISTORY_DAYS = 30

# Bailey/de-Prado (2014) statistical-sufficiency floor for the main-dashboard
# Bot-vs-Held Max Drawdown row/badges (DE-PERF-WINDOW-TRUTH-001 AC-1 re-scope,
# 2026-09-03). A SEPARATE constant from _PERFORMANCE_MIN_HISTORY_DAYS above --
# same underlying rationale, but a deliberately distinct name/binding so the
# Performance-tab quantstats banner and the dashboard MDD row never silently
# share one flag again (the AC-4 Decision-table mandate: these are two
# different questions -- "are these metrics statistically stable?" vs "does
# this window have the data it claims?" -- conflating them is what let the
# original F-2 mitigation decay silently once shadow_history crossed 30 days).
_MDD_MIN_OBS = 30
_PERFORMANCE_VALID_SCOPES = ("aggregate", "symphony")
# Calendar-window tokens /api/performance accepts for `days`, in the order the page's
# buttons show them; each resolves through analytics.window_cutoff (counted back from the
# session the figures belong to), the same cutoff the dashboard strip and hero chart use.
_PERFORMANCE_WINDOW_TOKENS = ("30d", "60d", "90d", "125d", "ytd", "1y", "5y")
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

# The one operator-facing time form, 'YYYY-MM-DD HH:MM ET': templates print a stored
# time through this filter, never by slicing its text. {{ v | et_stamp }},
# {{ v | et_stamp(date_only=True) }}, {{ v | et_stamp(naive_is_utc=False) }}.
app.add_template_filter(analytics.format_et_stamp, name="et_stamp")

# Raw stored enums (verdicts, observation subjects) read as words through ONE mapping. A value
# with no entry reads as its own words (underscores to spaces, sentence case), so a value no
# producer has written yet is never printed raw.
_ADVISOR_WORDS = {
    "CLEAR": "Clear",
    "WATCH": "Watch",
    "BREACH": "Breach",
    "ADOPT_CANDIDATE": "Adopt candidate",
    "KEEP_INCUMBENT": "Keep incumbent",
    "REJECT_VETO_FAILED": "Rejected: failed the overfitting check",
    "REJECT_FDR_FAILED": "Rejected: not significant after correction",
    "REJECT_NOT_A_WINNER": "Rejected: not the strongest candidate",
}


def advisor_words(value):
    """A stored verdict or subject as words: the mapped phrase, else the value's own words.

    A non-text value is returned as it is (the template's autoescape prints it).
    """
    if not isinstance(value, str):
        return value
    mapped = _ADVISOR_WORDS.get(value)
    if mapped is not None:
        return mapped
    text = " ".join(value.replace("_", " ").split()).lower()
    return text[:1].upper() + text[1:]


app.add_template_filter(advisor_words, name="advisor_words")


_BASIS_SOURCE_PARENTHETICAL = re.compile(r"(.*?) \(Composer, (.*)\)")


def retirement_basis(last_run):
    """The basis line of a retirement run in words, for the Retirement tab's last-run block.

    The run's own stored label is the text. For a Composer run the source is named first in
    words: the stored "realized daily returns (Composer, deposit-adjusted)" reads "Composer
    realized daily returns (deposit-adjusted)", and a label of another shape reads
    "Composer: <label>". Any other source prints the stored label as it is, and a run that
    stored none says so. The source token itself (``composer``) is never printed.
    """
    run = last_run if isinstance(last_run, dict) else {}
    label = run.get("basis_label")
    if not (isinstance(label, str) and label.strip()):
        return "basis not recorded"
    if run.get("source") != "composer":
        return label
    folded = _BASIS_SOURCE_PARENTHETICAL.fullmatch(label)
    if folded is not None:
        return f"Composer {folded.group(1)} ({folded.group(2)})"
    return f"Composer: {label}"


app.add_template_filter(retirement_basis, name="retirement_basis")

# ---------------------------------------------------------------------------
# Session cookie hardening (AC-10)
# ---------------------------------------------------------------------------
# These flags are set at startup; Flask honours them when writing the session
# cookie after each response.  SESSION_COOKIE_SECURE is env-driven so the
# operator can enable it when terminating TLS in a reverse proxy.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = env_flags.is_true(os.environ.get("SESSION_COOKIE_SECURE", ""))

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
# included so the login page renders its CSS/JS; 'login' is the sign-in route
# itself (logout needs a session); 'get_csrf_token' is safe (no sensitive data).
_AUTH_EXEMPT_ENDPOINTS: frozenset[str] = frozenset({"login", "static", "get_csrf_token", "health"})

# Session lifetime: a session ends this many hours after its login, and requests never extend it.
_SESSION_LIFETIME_HOURS_DEFAULT: int = 12  # hours
_SESSION_LIFETIME_HOURS_MAX: int = 720  # hours (30 days)


def _session_lifetime_hours(raw: str | None) -> int:
    """Parse DASHBOARD_SESSION_LIFETIME_HOURS: whole hours 1..720, else the 12 h default.

    A malformed value logs one warning that never prints the value.
    """
    if raw is None or raw == "":
        return _SESSION_LIFETIME_HOURS_DEFAULT
    if re.fullmatch(r"[0-9]+", raw, flags=re.ASCII):
        hours = int(raw)
        if 1 <= hours <= _SESSION_LIFETIME_HOURS_MAX:
            return hours
    logging.getLogger("alphabot").warning(
        "DASHBOARD_SESSION_LIFETIME_HOURS is not a whole number of hours from 1 to %d; using %d",
        _SESSION_LIFETIME_HOURS_MAX,
        _SESSION_LIFETIME_HOURS_DEFAULT,
    )
    return _SESSION_LIFETIME_HOURS_DEFAULT


def _session_now_ns() -> int:
    """The clock for session issue and expiry checks (a seam for tests)."""
    return time.time_ns()


_PROCESS_STARTED_NS: int = time.time_ns()

# Sessions issued at or before this instant are refused. None until first read from the database.
_session_revoked_before_ns: int | None = None
_SESSION_REVOCATION_LOCK = threading.Lock()


_SESSION_GENERATION_KEY = "session_generation"
# Logouts so far; loaded together with the floor, whenever the floor is None.
_session_generation: int = 0


def _load_revocation_state_locked() -> None:
    """Read the floor and the generation once per process; the caller holds the lock.

    An unreadable record refuses every session issued before this process started: the floor
    becomes the start instant (a new login is stamped past it) and the generation 0.
    """
    global _session_revoked_before_ns, _session_generation
    if _session_revoked_before_ns is not None:
        return
    try:
        floor = database.get_session_revocation_floor_ns()
        generation = database.get_session_generation()
    except Exception as exc:
        _daemon_log.error(
            "Session revocation record unreadable (%s); refusing sessions issued"
            " before this process started",
            type(exc).__name__,
        )
        floor, generation = _PROCESS_STARTED_NS, 0
    _session_generation = generation
    _session_revoked_before_ns = floor


def _revocation_state() -> tuple[int, int]:
    """The (floor, generation) pair, read together under the lock."""
    with _SESSION_REVOCATION_LOCK:
        _load_revocation_state_locked()
        return _session_revoked_before_ns, _session_generation


def _revocation_floor_ns() -> int:
    """The revocation floor, read from the database once per process."""
    return _revocation_state()[0]


def _revoke_sessions_issued_before(now_ns: int) -> bool:
    """End every session issued before now_ns; False when the stored record could not be written.

    The in-memory floor and generation are raised first, so the running process refuses
    earlier sessions either way. The floor is at least one past the previous floor, so a
    session stamped at the previous floor is ended too; the generation ends sessions whose
    stamp lies ahead of the clock after a backward step.
    """
    global _session_revoked_before_ns, _session_generation
    with _SESSION_REVOCATION_LOCK:
        _load_revocation_state_locked()
        floor = max(now_ns, _session_revoked_before_ns + 1)
        _session_revoked_before_ns = floor
        _session_generation += 1
        generation = _session_generation
    try:
        # One statement stores both, so a logout is fully durable or reported not durable.
        database.record_session_logout(floor, generation)
    except Exception as exc:
        _daemon_log.error(
            "Session revocation could not be stored (%s); earlier sessions end only until"
            " the daemon restarts",
            type(exc).__name__,
        )
        return False
    return True


# How far an issue time may lie beyond max(now, floor + 1) and still be served.
_SESSION_ISSUE_FUTURE_TOLERANCE_NS: int = 60 * 1_000_000_000  # nanoseconds (60 s)


def _session_issue_is_valid(
    issued: object, logged_in_at: object, now_ns: int, generation: object
) -> bool:
    """True for an integer issue time after the revocation floor and not beyond the next
    stamp a login could write, with a login time that is inside the lifetime.

    The issue time orders sessions against revocation (a login after a backward clock step is
    stamped floor + 1, ahead of the clock); the lifetime counts from the wall-clock login time.
    """
    if not isinstance(generation, int) or isinstance(generation, bool):
        return False
    if logged_in_at is None:
        # A session written before the login time was recorded counts from its issue time.
        logged_in_at = issued
    for value in (issued, logged_in_at):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    # A login stamps issued = max(login time, floor + 1), so a login time past it is forged.
    if logged_in_at > issued:
        return False
    floor, current_generation = _revocation_state()
    if generation != current_generation or issued <= floor:
        return False
    if issued > max(now_ns, floor + 1) + _SESSION_ISSUE_FUTURE_TOLERANCE_NS:
        return False
    lifetime_ns = int(app.permanent_session_lifetime.total_seconds() * 1_000_000_000)
    return now_ns - logged_in_at <= lifetime_ns


def _session_is_valid() -> bool:
    """True when the session is authenticated, carries a sane issue time after the
    revocation floor, and is inside the lifetime."""
    return session.get("authenticated") is True and _session_issue_is_valid(
        session.get("issued_at_ns"),
        session.get("logged_in_at_ns"),
        _session_now_ns(),
        session.get(_SESSION_GENERATION_KEY),
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

    # Exempt login/static so those routes remain reachable pre-auth.
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
    if _session_is_valid():
        return None

    # Not authenticated — redirect HTML requests to login, 401 for API/XHR.
    if _is_api_or_xhr():
        return jsonify({"error": "unauthenticated"}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page: GET renders the form; POST processes the credential."""
    if request.method != "POST":
        # Already authenticated — send to dashboard (AC-13).
        if _session_is_valid():
            return redirect(url_for("dashboard"))
        notice = (
            _LOGOUT_NOT_DURABLE_TEXT
            if session.get("logout_notice") == _LOGOUT_NOT_DURABLE_FLAG
            else None
        )
        return render_template("login.html", csrf_token=_CSRF_TOKEN, error=notice)

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
    # max(): a login in the same clock tick as a logout must still outrank the floor.
    login_ns = _session_now_ns()
    floor, generation = _revocation_state()
    session["issued_at_ns"] = max(login_ns, floor + 1)
    session[_SESSION_GENERATION_KEY] = generation
    session["logged_in_at_ns"] = login_ns
    return redirect(url_for("dashboard"))


_LOGOUT_NOT_DURABLE_FLAG = "revocation_not_durable"
_LOGOUT_NOT_DURABLE_TEXT = "Signed out. Other sessions are ended until the daemon restarts."


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """GET shows the confirm page; POST ends every session issued before now."""
    if request.method != "POST":
        return render_template("login.html", csrf_token=_CSRF_TOKEN, confirm_logout=True)
    durable = _revoke_sessions_issued_before(_session_now_ns())
    session.clear()
    if not durable:
        session["logout_notice"] = _LOGOUT_NOT_DURABLE_FLAG
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


# Values of the Sec-Fetch-Site request header that name a page of another origin.  "same-site"
# is a page on a sibling host of the same registrable domain: the deployed host sits under a
# domain that strangers share, so such a page is same-site with the dashboard and its form POSTs
# carry the session cookie.  "same-origin", "none" (typed or bookmarked) and an absent header
# (script clients, older browsers) are not refused by this rule.
_REFUSED_FETCH_SITES = frozenset({"same-site", "cross-site"})


def _validate_csrf() -> None:
    """Reject POST requests that lack the correct CSRF token.

    Two acceptance channels:
    - X-CSRF-Token request header — used by fetch()/XHR callers (JSON POSTs
      from the dashboard JS) on every route.  Browsers block cross-site scripts
      from setting arbitrary request headers, so the header itself acts as the
      synchronizer token for those callers.
    - csrf_token form field — the LOGIN endpoint only: the native browser form
      POST on the login page cannot set custom headers.  The token is public
      (GET /api/csrf-token serves it, as does the login page), so on any other
      route a form field would let a form posted from a sibling-host page
      through for every route that reads no JSON body.
      The field is read only for form-encoded content types
      (application/x-www-form-urlencoded, multipart/form-data).  Accessing
      request.form on a JSON POST triggers Werkzeug body parsing, which enforces
      MAX_CONTENT_LENGTH before this CSRF check can fire — breaking the
      CSRF-before-body-size guard ordering invariant.

    A POST whose Sec-Fetch-Site is same-site or cross-site is refused whatever
    the token.  The token is compared as bytes: compare_digest raises on a
    str that is not ASCII, which would answer 500 instead of 403.

    No new pip dependencies required.
    """
    if not _csrf_check_enabled:
        return
    fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().lower()
    if fetch_site in _REFUSED_FETCH_SITES:
        _daemon_log.warning(
            "CSRF check refused %s %s (Sec-Fetch-Site: %s)",
            request.method,
            request.path,
            fetch_site,
        )
        abort(403, description="Cross-site request refused")
    # Header channel: fetch()/XHR callers set X-CSRF-Token.
    token = request.headers.get("X-CSRF-Token", "")
    # Form-field channel: the login page's native form POST only.  Gate on
    # content-type so we never touch request.form on JSON requests — that
    # would trigger body parsing and fire 413 before we can return 403.
    if not token and request.endpoint == "login":
        ct = request.content_type or ""
        if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
            token = request.form.get("csrf_token", "")
    if not secrets.compare_digest(token.encode("utf-8", "replace"), _CSRF_TOKEN.encode("utf-8")):
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


def _peer_is_loopback(addr: str | None) -> bool:
    """True when ``addr`` is a loopback address (IPv4-mapped IPv6 unwrapped); False on any garbage."""
    try:
        ip = ipaddress.ip_address(addr)
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        return bool(ip.is_loopback)
    except (ValueError, TypeError):
        return False


def _health_caller_is_on_box() -> bool:
    """On box = loopback peer AND no proxy header; header presence decides, never its value."""
    return (
        "X-Forwarded-For" not in request.headers
        and "Forwarded" not in request.headers
        and _peer_is_loopback(request.remote_addr)
    )


@app.route("/health")
def health():
    """Minimal unauthenticated liveness probe (F-005).

    Two caller classes: an on-box caller (loopback peer, no forwarding header) gets the full
    body; every other caller gets only status, daemon_started_at and last_successful_cycle_at.

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
    body = {
        **_health_view(_state, _now_et()),
        "daemon_started_at": _DAEMON_STARTED_AT,
        "last_successful_cycle_at": _state.get("last_successful_cycle_at"),
    }
    if not _health_caller_is_on_box():
        # Proxied or remote callers get liveness only; the status was computed from the full view.
        body = {k: body[k] for k in ("status", "daemon_started_at", "last_successful_cycle_at")}
    return jsonify(body)


@app.route("/api/header-status")
def header_status():
    """The header's light status read for every page but ``/`` (under 10 KB, one state read).

    Same field names as ``/api/state`` so the header functions read either body unchanged. Read-only:
    the state read opens no write connection. The Eastern clock is read once, through ``_now_et``, so
    the market state, the phase and the countdown agree. Behind the auth gate, so a lapsed session
    answers 401 JSON, which sends the page to the login form.
    """
    try:
        now_et = _now_et()
        market_state = get_market_state(now_et)
        state = database.load_state() or {}
        last_cycle_at = state.get("last_successful_cycle_at")
        next_run_seconds = _seconds_to_next_minute(now_et)
        meta = _build_meta({}, next_run_seconds=next_run_seconds, market_state=market_state)
        return jsonify(
            {
                **_health_view(state, now_et),
                "daemon_started_at": _DAEMON_STARTED_AT,
                "last_successful_cycle_at": last_cycle_at,
                "market_state": market_state,
                "engine_phase": _engine_phase(now_et, market_state, last_cycle_at),
                "next_run_seconds": next_run_seconds,
                "meta": {
                    key: meta[key] for key in ("mode", "system_online", "account", "next_run")
                },
            }
        )
    except Exception as exc:
        _daemon_log.error("header status failed: %s", type(exc).__name__)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)
# werkzeug's own request log stays off; the middleware writes the one access line per request.
app.wsgi_app = timing_log.AccessLogMiddleware(app.wsgi_app)

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
# Assigned here, not beside the helper, so an invalid-value warning reaches the daemon log file.
app.permanent_session_lifetime = timedelta(
    hours=_session_lifetime_hours(os.environ.get("DASHBOARD_SESSION_LIFETIME_HOURS"))
)
# The tenure review's run path logs one INFO line per Composer request; these loggers are
# not children of "alphabot", so the daemon's file handler is attached to each directly.
for _run_path_logger_name in (
    "advisors.composer_portfolio_client",
    "advisors.composer_backtest_client",
    "advisors.tenure_review_runner",
):
    _run_path_logger = logging.getLogger(_run_path_logger_name)
    _run_path_logger.setLevel(logging.INFO)
    _run_path_logger.addHandler(_daemon_fh)
    # Without any handler the logging module's last resort printed warnings to standard error
    # (the journal); the file handler removes it, so a WARNING-level stream handler restores it
    # without sending the per-request INFO lines there.
    _run_path_stderr = logging.StreamHandler()
    _run_path_stderr.setLevel(logging.WARNING)
    _run_path_logger.addHandler(_run_path_stderr)


# The line a login form shows when it was rendered by an earlier process: the token it holds
# belongs to a daemon that has since restarted.
_LOGIN_FORM_EXPIRED_LINE = "This form expired after a restart; sign in again"


@app.before_request
def _csrf_before_request():
    """Enforce CSRF token on every mutating (POST) request (A-3).

    A refused login form is answered with the login page itself (status 403, the current process
    token, an expired-form line) instead of Werkzeug's raw 403 page. The credential is not read, so
    the throttle does not count it and nothing is signed in. Every other endpoint keeps the raw 403.
    """
    if request.method == "POST":
        try:
            _validate_csrf()
        except Forbidden:
            if request.endpoint == "login":
                return render_template(
                    "login.html", csrf_token=_CSRF_TOKEN, error=_LOGIN_FORM_EXPIRED_LINE
                ), 403
            raise
    return None


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
# The account totals latched for one close snapshot: {"key": (trading_day, captured_at_et),
# "totals": dict, "as_of": str}. Set by _refresh_account_totals from the first successful
# read that began at or after the snapshot's capture moment; the frozen strip reads it so
# nothing shown after the close moves with later refreshes. This is the in-memory copy of the
# latch: it is stored in the close_account_totals table when taken, and a restart, which empties
# this copy, restores it from there (_close_totals_for).
_frozen_account_totals: dict | None = None
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
# Monotonic-clock twin of _account_totals_last_success_at, used for the grace age
# (immune to wall-clock steps). Set only after a refresh fully succeeded.
_monotonic = time.monotonic
_account_totals_last_success_mono: float | None = None
# Seconds a successful account fetch keeps serving as current on the live branch
# before the stale chip shows: covers one missed 60 s cycle plus jitter.
_ACCOUNT_BASIS_STALE_AFTER_S = 90
# One account refresh in flight at a time (non-blocking slot, freed in the thread's finally).
_ACCOUNT_REFRESH_FLIGHT = threading.Lock()


def _account_basis_past_grace() -> bool:
    """True when no refresh has succeeded in this process or the last one is >= the grace old."""
    stamp = _account_totals_last_success_mono
    return stamp is None or _monotonic() - stamp >= _ACCOUNT_BASIS_STALE_AFTER_S


def _cycle_refresh_body() -> None:
    """Refresh after an engine run unless another refresh is already in flight."""
    if not _ACCOUNT_REFRESH_FLIGHT.acquire(blocking=False):
        _daemon_log.info("cycle refresh not started: a refresh is already in flight")
        return
    try:
        _refresh_account_totals()
    finally:
        _ACCOUNT_REFRESH_FLIGHT.release()


def _start_account_refresh(reason: str) -> bool:
    """Run _refresh_account_totals on a daemon thread unless one is already in flight."""
    if not _ACCOUNT_REFRESH_FLIGHT.acquire(blocking=False):
        _daemon_log.info("account refresh (%s) not started: a refresh is already in flight", reason)
        return False

    def _body() -> None:
        try:
            _refresh_account_totals()
        finally:
            _ACCOUNT_REFRESH_FLIGHT.release()

    try:
        threading.Thread(target=_body, name=f"account-refresh-{reason}", daemon=True).start()
    except BaseException:
        _ACCOUNT_REFRESH_FLIGHT.release()
        raise
    return True


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
# Seconds of SSE silence before a keep-alive comment; read at call time so tests can patch it.
_SSE_HEARTBEAT_S = 15


@dataclass(frozen=True)
class CycleRecord:
    """One engine spawn: when it ran, how it ended and whether it wrote anything."""

    cycle_id: str
    started_at: str
    completed_at: str
    outcome: str
    changed: bool
    exit_code: int | None
    running_cycle_id: str | None
    running_since: str | None

    def event_payload(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcome": self.outcome,
            "changed": self.changed,
        }


def _now_et() -> datetime:
    return datetime.now(_ET)


# Scheduled engine pause windows: the session start runs its first cycle for a few minutes.
_OPENING_PHASE_MINUTES = 5  # minutes after 09:30 ET during which the first cycle is expected


def _engine_phase(now_et: datetime, market_state: str, last_cycle_at) -> str:
    """Name the engine phase: closed, blackout, opening or running (pure, no clock read)."""
    if market_state != "open":
        return "closed"
    day = now_et.date()
    if (
        market_calendar.rebalance_blackout_start(day)
        <= now_et.time()
        < market_calendar.session_close(day)
    ):
        return "blackout"
    open_at = datetime.combine(day, dt_time(9, 30), tzinfo=_ET)
    if open_at <= now_et < open_at + timedelta(minutes=_OPENING_PHASE_MINUTES):
        try:
            cycle_dt = datetime.fromisoformat(str(last_cycle_at).replace("Z", "+00:00"))
            if cycle_dt.tzinfo is None:
                cycle_dt = cycle_dt.replace(tzinfo=_ET)
            first_cycle_done = cycle_dt >= open_at
        except (TypeError, ValueError):
            first_cycle_done = False
        if not first_cycle_done:
            return "opening"
    return "running"


_cycle_id_lock = threading.Lock()
_cycle_id_counters: dict[str, int] = {}


def _mint_cycle_id(started_at: datetime) -> str:
    second = started_at.replace(microsecond=0).isoformat()
    with _cycle_id_lock:
        n = _cycle_id_counters.get(second, 0) + 1
        _cycle_id_counters.clear()  # only the current second needs a counter
        _cycle_id_counters[second] = n
    return f"{second}#{n}"


def _classify_outcome(started_at: datetime, exit_code: int | None, terminated: bool = False) -> str:
    """Name the engine's branch for a spawn at *started_at*, using its own comparisons.

    A run the daemon ended (see _engine_run_overdue) is "terminated" whatever status it left.
    """
    if terminated:
        return "terminated"
    if exit_code is None or exit_code != 0:
        return "failed"
    day = started_at.date()
    now = started_at.time()
    if (
        not market_calendar.is_trading_day(day)
        or now < dt_time(9, 30)
        or now > market_calendar.post_mortem_cutoff(day)
    ):
        return "closed"
    if market_calendar.rebalance_blackout_start(day) <= now < market_calendar.session_close(day):
        return "blackout"
    if now >= market_calendar.session_close(day):
        return "eod"
    return "cycle"


def _post_mortem_signature(started_at: datetime):
    path = os.path.join(analytics._POST_MORTEMS_DIR, f"post_mortem_{started_at.date()}.json")
    try:
        st = os.stat(path)
        return (True, st.st_mtime_ns, st.st_size)
    except OSError:
        return (False, None, None)


def _read_markers():
    try:
        return database.read_cycle_markers()
    except Exception:
        return (None, None)


def _markers_changed(before, after, sig_before, sig_after) -> bool:
    if before == (None, None) or after == (None, None):
        return True  # an unobservable state is reported as changed
    return before != after or sig_before != sig_after


# --- Engine run slot and scheduler liveness ---------------------------------------------
# Thresholds for the /health verdict; the engine lock is never read here.
_HEALTH_SCHEDULER_STALL_S = 120  # seconds without a scheduler tick (two missed minutes)
_HEALTH_ENGINE_OVERRUN_S = 180  # seconds one engine run may last before it reads as overrun
_HEALTH_FAILING_RUNS = 5  # consecutive failed spawned runs that read as failing

# The engine run bound. A run holds the single run slot for the whole engine process, so a run
# that never exits would stop every later cycle until a person restarted the daemon (a wedged
# Friday autotune would block Monday's open). The daemon therefore ends a run, and frees the slot:
#   * a run begun before the close of a trading day (any hour of it) and alive over
#     _ENGINE_SESSION_BOUND_S, unless it was forced;
#   * a run begun on an earlier ET date, from _ENGINE_EARLIER_DATE_END_AT on a trading day, so the
#     open is never blocked (the Friday autotune runs for hours and is left alone until then).
# A run begun after the close, or on a day the market never opens, is bounded only by the second
# rule. Ending a run is terminate, then kill _ENGINE_TERMINATE_GRACE_S later if it is still there.
_ENGINE_SESSION_BOUND_S = 600
_ENGINE_EARLIER_DATE_END_AT = dt_time(9, 25)
_ENGINE_TERMINATE_GRACE_S = 10
_ENGINE_REAP_AFTER_KILL_S = 30
_ENGINE_WAIT_POLL_S = 1.0  # how often a run in flight is checked against the bound

# How long a scheduler pass waits for a run whose process has already exited to finish its
# bookkeeping (marker reads, the published record) before the minute's spawn is tried.
_ENGINE_FINISH_WAIT_S = 2.0

_DAEMON_STARTED_MONO: float = _monotonic()
_ENGINE_RUN_LOCK = threading.Lock()
_engine_run: "EngineRun | None" = None
# (cycle_id, process handle, event set when the run's thread is done) of the run in flight, once
# its process has started.
_engine_process: "tuple[str, object, threading.Event] | None" = None
_last_cycle_record: CycleRecord | None = None
_RECENT_RUN_OUTCOMES: "_collections.deque[str]" = _collections.deque(maxlen=_HEALTH_FAILING_RUNS)
_scheduler_last_tick_mono: float | None = None
_scheduler_last_tick_at: str | None = None
_scheduler_started_mono: float | None = None


@dataclass(frozen=True)
class EngineRun:
    """The engine run in flight: its id, ET start and monotonic start."""

    cycle_id: str
    started_at: datetime
    started_mono: float


def _begin_engine_run(started_at: datetime) -> "EngineRun | None":
    """Take the single run slot, or return None when a run is already in flight."""
    global _engine_run
    with _ENGINE_RUN_LOCK:
        if _engine_run is not None:
            return None
        _engine_run = EngineRun(_mint_cycle_id(started_at), started_at, _monotonic())
        return _engine_run


def _end_engine_run(cycle_id: str) -> None:
    """Free the slot only when it still belongs to *cycle_id*."""
    global _engine_run, _engine_process
    with _ENGINE_RUN_LOCK:
        if _engine_run is not None and _engine_run.cycle_id == cycle_id:
            _engine_run = None
            _engine_process = None


def _note_engine_process(cycle_id: str, proc, done: threading.Event) -> None:
    """Record the process of the run holding the slot, so a scheduler pass can see it has exited."""
    global _engine_process
    with _ENGINE_RUN_LOCK:
        if _engine_run is not None and _engine_run.cycle_id == cycle_id:
            _engine_process = (cycle_id, proc, done)


def _reap_exited_engine_run() -> None:
    """Let a run whose process has already exited finish its bookkeeping before the minute spawns.

    The run's thread frees the slot and publishes its record after its process exits, so for a
    moment the slot is taken by a run that is over. A scheduler pass waits for that (at most
    _ENGINE_FINISH_WAIT_S) so the minute's spawn finds the slot free instead of being counted as
    skipped. A run whose process is still alive is never waited for. Never raises.
    """
    held = _engine_process
    if held is None:
        return
    try:
        if held[1].poll() is not None:
            held[2].wait(_ENGINE_FINISH_WAIT_S)
    except Exception as exc:
        _daemon_log.warning("exited engine run not awaited: %s", type(exc).__name__)


def _engine_run_state() -> "EngineRun | None":
    return _engine_run


# Monotonic start of each in-flight engine run, read once by _publish_cycle for duration_s.
_cycle_started_mono: dict[str, float] = {}


def _log_skipped_minute(record: CycleRecord) -> None:
    running = _engine_run_state()
    running_s = _monotonic() - running.started_mono if running is not None else None
    timing_log.emit(
        "cycle",
        cycle_id=record.cycle_id,
        outcome="skipped",
        started_at=record.started_at,
        running_cycle_id=record.running_cycle_id,
        running_since=record.running_since,
        running_s=None if running_s is None else f"{running_s:.1f}",
    )


def _log_finished_cycle(record: CycleRecord) -> None:
    started_mono = _cycle_started_mono.pop(record.cycle_id, None)
    duration = None if started_mono is None else f"{_monotonic() - started_mono:.1f}"
    timing_log.emit(
        "cycle",
        cycle_id=record.cycle_id,
        outcome=record.outcome,
        started_at=record.started_at,
        ended_at=record.completed_at,
        duration_s=duration,
        exit_code=record.exit_code,
    )


def _publish_cycle(record: CycleRecord) -> None:
    global _last_cycle_record
    _last_cycle_record = record
    if record.outcome == "skipped":
        _log_skipped_minute(record)
    else:
        _log_finished_cycle(record)
        _RECENT_RUN_OUTCOMES.append(record.outcome)
    _notify_cycle_complete(record)


def _notify_cycle_complete(record: CycleRecord) -> None:
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
    if record.outcome != "skipped":
        # Step 1: atomically mask stale data from all lock-free readers.
        _account_totals_cache.mark_stale()

        # Step 2: start the refresh before the fan-out so it has maximum lead time.
        # _refresh_account_totals never raises (D-1 contract); daemon=True so it
        # does not prevent process exit.  Single flight with the minute refresh: a
        # Composer call already in flight serves this cycle too.
        threading.Thread(target=_cycle_refresh_body, daemon=True, name="cycle-refresh").start()

    # Step 3: fan out SSE notification (O(1) per client, no I/O).
    payload = json.dumps(record.event_payload())
    with _sse_clients_lock:
        clients = list(_sse_clients)

    for q in clients:
        try:
            q.put_nowait(("cycle-complete", payload))
        except Exception:
            pass  # full or closed — skip, do not raise


def _start_engine_process(cmd, env, log_fh):
    """Start the engine as a child process and return its handle: the daemon's one engine spawn.

    A handle (not `subprocess.run`) so a run that hangs can be ended; the engine writes its
    output to the one daemon log through *log_fh*."""
    return subprocess.Popen(cmd, env=env, stdout=log_fh, stderr=log_fh)


def _engine_run_overdue(
    started_at: datetime, started_mono: float, force: bool, now_et: datetime, now_mono: float
) -> str | None:
    """Why the engine run begun at *started_at* must now be ended, or None (pure, no clock read).

    "earlier_date": a run begun on an earlier ET date, on a trading day from 09:25 ET. A run begun
    after the close, or on a day the market never opens, is not reached by the session rule below,
    so the open is the only thing that ends it.
    "session_bound": a run begun before the close of a trading day, alive for _ENGINE_SESSION_BOUND_S
    or more, unless it was forced. Never raises: a calendar fault ends nothing.
    """
    try:
        today = now_et.date()
        if (
            started_at.date() < today
            and market_calendar.is_trading_day(today)
            and now_et.time().replace(tzinfo=None) >= _ENGINE_EARLIER_DATE_END_AT
        ):
            return "earlier_date"
        began_day = started_at.date()
        if (
            not force
            and market_calendar.is_trading_day(began_day)
            and started_at.time().replace(tzinfo=None) < market_calendar.session_close(began_day)
            and now_mono - started_mono >= _ENGINE_SESSION_BOUND_S
        ):
            return "session_bound"
    except Exception as exc:
        _daemon_log.warning("engine run bound not evaluated: %s", type(exc).__name__)
    return None


def _end_engine_process(proc, cycle_id: str, reason: str) -> int | None:
    """Terminate the engine process, kill it if it is still there _ENGINE_TERMINATE_GRACE_S later.

    Returns its exit status (None when it could not be reaped). The run slot is freed by the caller
    only after this returns, so a replacement never starts beside a live engine."""
    _daemon_log.warning("engine run %s terminated by the daemon: %s", cycle_id, reason)
    try:
        proc.terminate()
    except OSError:
        pass  # already gone
    try:
        return proc.wait(timeout=_ENGINE_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        _daemon_log.warning(
            "engine run %s ignored terminate for %s s: killed", cycle_id, _ENGINE_TERMINATE_GRACE_S
        )
    try:
        proc.kill()
    except OSError:
        pass
    try:
        return proc.wait(timeout=_ENGINE_REAP_AFTER_KILL_S)
    except subprocess.TimeoutExpired:
        _daemon_log.error("engine run %s was not reaped after kill", cycle_id)
        return None


def _wait_for_engine(
    proc, cycle_id: str, started_at: datetime, started_mono: float, force: bool
) -> tuple[int | None, bool]:
    """Wait for the engine process to exit, ending it when the run bound says so.

    Returns (exit status, whether the daemon ended it). Polled every _ENGINE_WAIT_POLL_S on the
    engine thread, never on the scheduler's."""
    while True:
        try:
            return proc.wait(timeout=_ENGINE_WAIT_POLL_S), False
        except subprocess.TimeoutExpired:
            pass
        reason = _engine_run_overdue(started_at, started_mono, force, _now_et(), _monotonic())
        if reason is not None:
            return _end_engine_process(proc, cycle_id, reason), True


def trigger_alpha_bot(force=False, *, cycle_id=None, started_at=None):
    started_at = started_at if started_at is not None else _now_et()
    cycle_id = cycle_id if cycle_id is not None else _mint_cycle_id(started_at)
    started_mono = _monotonic()
    _cycle_started_mono[cycle_id] = started_mono
    exit_code = None
    terminated = False
    proc = None
    done = threading.Event()
    before = _read_markers()
    sig_before = _post_mortem_signature(started_at)
    try:
        # Inside the try: a fault on this line must not leak the run slot.
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Triggering Alpha Bot...")
        cmd = [sys.executable, "alpha_bot_execution.py"]
        if force:
            cmd.append("--force")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        with open(LOG_FILE, "a", encoding="utf-8") as log_fh:
            proc = _start_engine_process(cmd, env, log_fh)
            _note_engine_process(cycle_id, proc, done)
            exit_code, terminated = _wait_for_engine(
                proc, cycle_id, started_at, started_mono, force
            )
        if exit_code != 0 and not terminated:
            failure = subprocess.CalledProcessError(exit_code, cmd)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Execution failed: {failure}")
    except Exception as exc:
        # The engine could not be started or watched: the record below reads failed and the slot
        # frees, so a process this run left behind is ended first (the next minute starts clean).
        _daemon_log.error("engine run %s did not complete: %s", cycle_id, type(exc).__name__)
        try:
            if proc is not None and proc.poll() is None:
                _end_engine_process(proc, cycle_id, "run could not be watched")
        except Exception as end_exc:
            _daemon_log.error("engine run %s left behind: %s", cycle_id, type(end_exc).__name__)
    finally:
        try:
            _end_engine_run(cycle_id)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "engine slot release failed: %s", type(exc).__name__
            )
        try:
            after = _read_markers()
            changed = _markers_changed(
                before, after, sig_before, _post_mortem_signature(started_at)
            )
            try:
                outcome = _classify_outcome(started_at, exit_code, terminated)
            except Exception as exc:
                # A calendar fault must not stop the publish; the state is unknown, so
                # the record claims a change and clients refetch.
                logging.getLogger(__name__).warning(
                    "cycle outcome unclassified: %s", type(exc).__name__
                )
                outcome = "terminated" if terminated else ("failed" if exit_code else "cycle")
                changed = True
            _publish_cycle(
                CycleRecord(
                    cycle_id=cycle_id,
                    started_at=started_at.isoformat(),
                    completed_at=_now_et().isoformat(),
                    outcome=outcome,
                    changed=changed,
                    exit_code=exit_code,
                    running_cycle_id=None,
                    running_since=None,
                )
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("cycle publish failed: %s", type(exc).__name__)
        finally:
            done.set()


def threaded_trigger():
    started_at = _now_et()
    run = _begin_engine_run(started_at)
    if run is None:
        running = _engine_run_state()
        instant = started_at.isoformat()
        _publish_cycle(
            CycleRecord(
                cycle_id=_mint_cycle_id(started_at),
                started_at=instant,
                completed_at=instant,
                outcome="skipped",
                changed=False,
                exit_code=None,
                running_cycle_id=running.cycle_id if running else None,
                running_since=running.started_at.isoformat() if running else None,
            )
        )
        return
    try:
        threading.Thread(
            target=trigger_alpha_bot,
            kwargs={"cycle_id": run.cycle_id, "started_at": run.started_at},
            daemon=True,
            name="engine-cycle",
        ).start()
    except Exception as exc:
        _end_engine_run(run.cycle_id)
        _daemon_log.error("engine thread could not start: %s", type(exc).__name__)


class _GuardedJobCall(functools.partial):
    """A scheduled call that logs a fault by type and returns, so the slot is rescheduled."""

    label = ""

    def __call__(self, *args, **kwargs):
        try:
            return super().__call__(*args, **kwargs)
        except Exception as exc:
            _daemon_log.error("scheduled job %s failed: %s", self.label, type(exc).__name__)
            _daemon_log.debug("scheduled job %s traceback", self.label, exc_info=True)
            return None


def _guard(job, label: str):
    """Wrap the callable of *job* so a raising slot never stops the scheduler or siblings."""
    inner = job.job_func
    guarded = _GuardedJobCall(inner.func, *inner.args, **inner.keywords)
    guarded.label = label
    functools.update_wrapper(guarded, inner)
    job.job_func = guarded
    return job


def _scheduler_tick() -> None:
    global _scheduler_last_tick_mono, _scheduler_last_tick_at
    _reap_exited_engine_run()
    try:
        schedule.run_pending()
    except Exception as exc:
        _daemon_log.error("scheduler pass failed: %s", type(exc).__name__)
    _poke_closed_warmup()
    _scheduler_last_tick_mono = _monotonic()
    _scheduler_last_tick_at = _now_et().isoformat()


def _health_degraded_reasons(now_mono: float) -> list[str]:
    reasons: list[str] = []
    if _scheduler_last_tick_mono is not None:
        since = _scheduler_last_tick_mono
    elif _scheduler_started_mono is not None:
        since = _scheduler_started_mono
    else:
        since = _DAEMON_STARTED_MONO
    if now_mono - since >= _HEALTH_SCHEDULER_STALL_S:
        reasons.append("scheduler_stalled")
    run = _engine_run_state()
    if run is not None and now_mono - run.started_mono > _HEALTH_ENGINE_OVERRUN_S:
        reasons.append("engine_overrun")
    outcomes = list(_RECENT_RUN_OUTCOMES)
    if len(outcomes) >= _HEALTH_FAILING_RUNS and all(
        o == "failed" for o in outcomes[-_HEALTH_FAILING_RUNS:]
    ):
        reasons.append("engine_failing")
    # The newest finished run was one the daemon ended (see _engine_run_overdue): it reads until
    # the next run finishes, so the operator sees that a run was cut and that the engine recovered.
    if outcomes and outcomes[-1] == "terminated":
        reasons.append("engine_terminated")
    return reasons


def _health_status() -> dict:
    reasons = _health_degraded_reasons(_monotonic())
    run = _engine_run_state()
    last = _last_cycle_record
    return {
        "status": "degraded" if reasons else "ok",
        "degraded_reasons": reasons,
        "scheduler_last_tick_at": _scheduler_last_tick_at,
        "engine_running_since": run.started_at.isoformat() if run else None,
        "last_cycle": (
            {
                "cycle_id": last.cycle_id,
                "outcome": last.outcome,
                "completed_at": last.completed_at,
            }
            if last
            else None
        ),
    }


def _state_degraded_reasons(state: dict | None, now_et: datetime) -> list[str]:
    """Degraded reasons that rest on the stored state and the clock (pure given its arguments).

    no_cycle_since_open: the market is open, its first five minutes (the header's Opening phase)
    are over, and the last cycle the engine committed is older than the open. It clears with the
    next commit and is never raised while the market is closed. A state that holds no stamp says
    nothing (an engine that never committed has nothing to be older than the open).

    close_snapshot_missing: a session has closed and, _CLOSE_SNAPSHOT_GRACE after its close, the
    stored close snapshot is still an earlier session's although the engine committed cycles on
    that day. The close is the day's own (13:00 on an early-close day). While it lasts the page
    serves that session's last committed state (_session_view, "held"). A state with no dated
    snapshot (a new install, or a snapshot with no trading day) has none to be missing, so it
    never raises this.

    Never raises: a calendar or parse fault reports no reason.
    """
    reasons: list[str] = []
    try:
        state = state or {}
        stamp = _cycle_stamp_moment(state.get("last_successful_cycle_at"))
        if get_market_state(now_et) == "open":
            open_at = _DatetimeType.combine(
                now_et.date(), market_calendar.session_open(now_et.date()), tzinfo=_ET
            )
            opening_over = open_at + timedelta(minutes=_OPENING_PHASE_MINUTES)
            if stamp is not None and now_et >= opening_over and stamp < open_at:
                reasons.append("no_cycle_since_open")
            return reasons
        latest = market_calendar.most_recent_completed_session(now_et)
        if latest is None or stamp is None or stamp.date() < latest:
            return reasons
        snapshot = state.get("last_market_close_snapshot")
        stored_day = snapshot.get("trading_day") if isinstance(snapshot, dict) else None
        if (
            stored_day
            and str(stored_day) < latest.isoformat()
            and now_et >= _session_close_moment(latest) + _CLOSE_SNAPSHOT_GRACE
        ):
            reasons.append("close_snapshot_missing")
    except Exception as exc:
        _daemon_log.warning("state degraded reasons unavailable: %s", type(exc).__name__)
    return reasons


def _health_view(state: dict | None, now_et: datetime) -> dict:
    """_health_status() with the reasons that rest on the stored state merged in.

    _health_status() itself reads no database; the routes that hold the stored state add what it
    says (a missed morning start, a close snapshot that never arrived). Any reason makes the
    status "degraded", so the status and the reasons always agree.
    """
    health_status = _health_status()
    extra = [
        reason
        for reason in _state_degraded_reasons(state, now_et)
        if reason not in health_status["degraded_reasons"]
    ]
    if not extra:
        return health_status
    return {
        **health_status,
        "status": "degraded",
        "degraded_reasons": [*health_status["degraded_reasons"], *extra],
    }


def _degraded_reasons(state: dict | None = None, now_et: datetime | None = None) -> list[str]:
    """The health helper's degraded reasons for a ``/api/state`` body.

    With the stored state the poll already read, it also carries the reasons that rest on it
    (_state_degraded_reasons), so the header and /health say the same thing. A failed read gives an
    empty list, so the header never claims "Degraded" on a read error; the warning names the
    exception type only, never its text.
    """
    try:
        if state is None or now_et is None:
            reasons = _health_status()["degraded_reasons"]
        else:
            reasons = _health_view(state, now_et)["degraded_reasons"]
        return list(reasons)
    except Exception as exc:
        _daemon_log.warning("degraded_reasons unavailable: %s", type(exc).__name__)
        return []


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


# The capture part of the latch key of a built snapshot (_held_close_snapshot): it has no capture
# time of its own that the stored snapshot of the session will share.
_BUILT_SNAPSHOT_LATCH = "close-state"


def _close_totals_key(snapshot: dict) -> tuple:
    """The (trading_day, captured_at_et) key the totals of a close snapshot are latched under."""
    day = snapshot.get("trading_day")
    if snapshot.get("built"):
        return (day, _BUILT_SNAPSHOT_LATCH)
    return (day, snapshot.get("captured_at_et"))


def _close_totals_for(snapshot: dict) -> dict | None:
    """The account totals latched for a close snapshot, or None.

    The in-memory latch when it is for this snapshot; otherwise the one stored in the
    close_account_totals table, restored into memory (a restart empties memory, so the figures a
    closed market shows are the ones it showed before the restart). A stored snapshot also takes
    the totals latched for its session while its own was not stored yet (the built snapshot,
    _held_close_snapshot), so the figures do not move when the real one arrives. A legacy snapshot
    with no trading day or capture time has no stored latch. Never raises.
    """
    global _frozen_account_totals
    keys = [_close_totals_key(snapshot)]
    if not snapshot.get("built"):
        keys.append((snapshot.get("trading_day"), _BUILT_SNAPSHOT_LATCH))
    held = _frozen_account_totals
    if held is not None and held["key"] in keys:
        return held
    for key in keys:
        if not key[0] or not key[1]:
            continue
        stored = database.load_close_totals_latch(str(key[0]), str(key[1]))
        if isinstance(stored, dict) and isinstance(stored.get("totals"), dict):
            restored = {"key": key, "totals": stored["totals"], "as_of": stored.get("as_of")}
            _frozen_account_totals = restored
            return restored
    return None


def _close_totals_latch_key(started_at: datetime) -> tuple | None:
    """The (trading_day, captured_at_et) key to latch a read that began at `started_at`.

    The snapshot is the one the market's session is served from (_session_view): the stored one,
    or the one built from the last committed state while the session's own is not stored yet.

    None when nothing should latch: the market is open, the latch already holds the newest
    finished session's snapshot (no stored state is read then), the snapshot's latch is already
    stored (the first read taken for a snapshot is the one every restart finds), no snapshot is
    served, the read began before the snapshot's capture moment, or the lookup failed. A legacy
    snapshot (no trading day) has no capture moment, so the first read the process sees
    latches. Never raises.
    """
    try:
        market_state = get_market_state(started_at)
        if market_state == "open":
            return None
        held = _frozen_account_totals
        latest = market_calendar.most_recent_completed_session(started_at)
        if held is not None and latest is not None and held["key"][0] == latest.isoformat():
            return None
        state = database.load_state() or {}
        snapshot = _session_view(state, market_state, started_at).snapshot
        if not isinstance(snapshot, dict):
            return None
        key = _close_totals_key(snapshot)
        if held is not None and held["key"] == key:
            return None
        if _close_totals_for(snapshot) is not None:
            return None
        capture = _snapshot_capture_moment(snapshot)
        if capture is not None and started_at < capture:
            return None
        return key
    except Exception:
        return None


def _log_total_stats_call(started: float, **outcome) -> None:
    """One timing line per total-stats call: the route template, never the account id."""
    timing_log.emit(
        "composer",
        "GET",
        "/portfolio/accounts/{account}/total-stats",
        **outcome,
        ms=round((timing_log.clock() - started) * 1000),
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
    global _account_totals_last_success_mono, _frozen_account_totals
    try:
        started_at = datetime.now(_ET)
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
        _call_started = timing_log.clock()
        try:
            resp = requests.get(url, headers=headers, timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S)
        except Exception as _call_exc:
            _log_total_stats_call(_call_started, error=type(_call_exc).__name__)
            raise
        _log_total_stats_call(_call_started, status=resp.status_code)
        if resp.status_code == 200:
            data = resp.json()
            latch_key = _close_totals_latch_key(started_at)
            latched: dict | None = None
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
                if latch_key is not None:
                    _frozen_account_totals = {
                        "key": latch_key,
                        "totals": dict(_account_totals_last_good),
                        "as_of": datetime.now(_ET).strftime(_ACCOUNT_BASIS_TS_FMT),
                    }
                    latched = _frozen_account_totals
            # The latch is stored once taken, outside the cache lock: a restart finds it again.
            if latch_key is not None and latch_key[0] and latch_key[1]:
                try:
                    database.save_close_totals_latch(
                        str(latch_key[0]), str(latch_key[1]), latched["totals"], latched["as_of"]
                    )
                except Exception as store_exc:
                    _daemon_log.warning(
                        "close totals latch not stored: %s", type(store_exc).__name__
                    )
            # Advance the as-of timestamp only after all writes completed without error.
            _account_totals_last_success_at = datetime.now(_ET).strftime(_ACCOUNT_BASIS_TS_FMT)
            _account_totals_last_success_mono = _monotonic()
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
            type(_exc).__name__,
        )
        _daemon_log.debug("_refresh_account_totals detail: %s", _exc, exc_info=True)


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


def _model_warmup_worker() -> None:
    """Resolve both model knobs so page badges show display names (never raises).

    Runs off the request and execution paths. Only the exception type is logged
    at WARNING because exception text can carry credentials; the traceback goes
    to DEBUG.
    """
    try:
        client = ai_advisor._build_client()
        first = model_config.resolve(model_config.Knob.SUGGESTION, client)
        # One listing fills both families; after a failed listing a second
        # resolve would just send another request, so stop.
        if first.source != "unavailable":
            model_config.resolve(model_config.Knob.SYNTHESIS, client)
    except Exception as exc:
        _daemon_log.warning("Model warm-up failed: %s", type(exc).__name__)
        _daemon_log.debug("Model warm-up failure detail", exc_info=True)


def _start_model_warmup() -> None:
    """Run the model warm-up on a daemon thread; returns immediately."""
    threading.Thread(target=_model_warmup_worker, daemon=True, name="model-warmup").start()


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


def _regime_label_symphony_entries() -> dict:
    """The stored bot state's symphony entries (metadata keys excluded), read-only."""
    state = database.load_state(read_only=True)
    return {k: v for k, v in state.items() if _is_symphony_state_entry(v)}


def _regime_label_job_worker() -> None:
    """Run the daily regime label job. The lazy import keeps it off the execution path; only the
    exception class name is logged."""
    try:
        import regime_label_job  # lazy, not module-level

        regime_label_job.run_regime_label_job(_regime_label_symphony_entries)
    except Exception as exc:
        _daemon_log.error("Regime label job failed: %s", type(exc).__name__)


def _run_regime_label_tick() -> None:
    """Non-blocking daily off-hours wrapper for the regime label job."""
    threading.Thread(target=_regime_label_job_worker, daemon=True, name="regime-label").start()


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
    here -- see n_obs, which is an int and passes through unchanged).
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


# Run-stamped and moving-window keys: they change every run without changing what an
# explanation was written about, so the reuse comparison ignores them and the explainer is
# never shown them. window_first stays in the comparison (the pair's first day is evidence).
# Both producers stamp their rows with a run id of their own -- "run_id" (the recommender
# run, Cycle 2e) and "review_run_id" (the tenure review) -- and both are bookkeeping that
# scopes a card to its run, never evidence.
_EXPLAINER_WITHHELD_KEYS = frozenset(
    {
        "explanation",
        "run_id",
        "review_run_id",
        "as_of_trading_day",
        "explanation_written_on",
        "n_obs",
        "window_last",
        "window_days",
        "composite_window_first",
        "composite_window_last",
        "composite_window_days",
    }
)


def _canonical_evidence_snapshot(rec: dict) -> dict:
    """Return a rounded snapshot of every evidence field in `rec` EXCEPT
    "explanation" (the value being reused) and the pair-identity keys
    "candidate_id"/"sibling_id" (already matched by construction to find
    this prior row -- not citable evidence) and the run-stamped keys in
    _EXPLAINER_WITHHELD_KEYS. The explainer is shown a copy of the rec with those
    withheld keys removed, so the reuse comparison and the explainer's evidence
    ignore the same keys.

    Cycle 2e (retirement-visibility AC-4): "run_id" is excluded too. The 03:45
    tick (and a manual "Run now") stamps a FRESH run_id on every rec each run;
    it is bookkeeping that scopes a card to its run, never evidence the
    explainer cites. Left in the snapshot it would differ every night and
    defeat explanation reuse (re-introducing the nightly LLM spend the reuse
    gate exists to prevent) for a pair whose evidence is unchanged.

    PR#140 /code-review finding 1 (Cycle 2c, AC-4): the whole-dict design
    replaces a curated field-list comparison -- advisors.retirement_
    explainer._build_explain_messages json.dumps()s the WHOLE rec and
    instructs the LLM to ground claims in ALL supplied evidence, so a
    curated subset could miss a materially-drifted field (e.g.
    stressed_correlation, ci_lower/ci_upper, or an individual
    candidate_metrics/sibling_metrics value) the LLM could still cite. Every
    float is rounded recursively (including into nested metrics dicts); a
    key missing on either side of the comparison makes the two snapshots
    NOT equal (plain dict `==` is exact key-set + value equality) -- this
    subsumes the freshness-on-rename rule and the legacy-row rule for free,
    with zero special-casing. Future-proof against schema growth: a NEW
    raw_response field automatically joins the comparison.
    """
    excluded = {"explanation", "candidate_id", "sibling_id", *_EXPLAINER_WITHHELD_KEYS}
    snapshot = {k: v for k, v in rec.items() if k not in excluded}
    return _round_floats(snapshot, _MATERIAL_CHANGE_ROUND_NDIGITS)


def _retirement_row_producer(raw: object) -> str | None:
    """Which job wrote a recommendation: "review" (the tenure review, stamped with
    review_run_id), "recommender" (the recommender run, stamped with run_id), or None for a
    record that carries neither stamp (a row written before the stamps existed, or a record
    that has not been stamped yet).

    The two jobs write the same role for a pair they both flag, with evidence that is not the
    same record (a different basis label, and window fields only the review carries), so a row
    of one never equals a row of the other and only the same job's rows can be reused.
    """
    if not isinstance(raw, dict):
        return None
    if "review_run_id" in raw:
        return "review"
    if "run_id" in raw:
        return "recommender"
    return None


def _build_retirement_prior_row_map(rows: list[dict]) -> dict[tuple, dict]:
    """Build a {(candidate_id, sibling_id, producer): newest_matching_raw_response}
    map from a newest-first advisor_observations row list.

    PR#140 /code-review finding 2 (Cycle 2c, AC-4 efficiency): hoists the
    reuse-lookup to ONE query per tick instead of a fresh role-wide DB query
    per rec -- _retirement_recommender_tick_worker builds this map once from
    a single database.get_advisor_observations_for_role() call, then every
    rec's reuse decision is an O(1) dict lookup against it. Only the FIRST
    (i.e. newest, since `rows` is newest-first by id DESC) row per pair AND
    producer is kept -- matches the pre-existing "most recent prior row governs"
    contract, per job (_retirement_row_producer). Malformed rows (non-dict
    raw_response) are skipped, never raised.
    """
    prior_map: dict[tuple, dict] = {}
    for row in rows:
        raw = row.get("raw_response")
        if not isinstance(raw, dict):
            continue
        key = (raw.get("candidate_id"), raw.get("sibling_id"), _retirement_row_producer(raw))
        if key not in prior_map:
            prior_map[key] = raw
    return prior_map


def _retirement_prior_raw_for(rec: dict, prior_map: dict) -> dict | None:
    """The newest stored row of the same pair written by the same job as `rec`.

    A row from the other job is never returned. A row that carries neither job's stamp is the
    fallback: it is nobody's in particular (it predates the stamps), and each job then replaces
    it with a row of its own.
    """
    pair = (rec.get("candidate_id"), rec.get("sibling_id"))
    producer = _retirement_row_producer(rec)
    prior_raw = prior_map.get((*pair, producer))
    if prior_raw is None and producer is not None:
        prior_raw = prior_map.get((*pair, None))
    return prior_raw


def _retirement_find_reusable_prior_explanation(rec: dict, prior_map: dict) -> str | None:
    """Return a prior explanation text reusable for `rec`, or None to force a
    fresh explain_recommendation call (Cycle 2c, AC-4: nightly-explain spend
    control).

    `prior_map` is a {(candidate_id, sibling_id, producer): newest_matching_raw_response}
    map pre-built ONCE per tick via _build_retirement_prior_row_map (PR#140
    review finding 2) -- an O(1) lookup here, no DB access, no per-rec query.
    The row compared is the newest one written by the SAME job as `rec`
    (_retirement_prior_raw_for), never the other job's.

    Reuse-eligible iff the prior row's explanation is truthy AND
    _canonical_evidence_snapshot(rec) == _canonical_evidence_snapshot(prior_raw)
    (PR#140 review finding 1 -- see that function's docstring for the full
    rationale). Fail-open: the whole lookup + comparison is ONE unit -- ANY
    exception anywhere inside it resolves to "no match", i.e. returns None,
    identically to the "no prior history" case. Never propagates.
    """
    try:
        prior_raw = _retirement_prior_raw_for(rec, prior_map)
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


# Cycle 2e (retirement-visibility AC-4): the advisor_observations role under
# which every retirement run (the 03:45 tick AND a manual "Run now") persists
# exactly ONE summary row, so the dashboard can show that a run happened and
# what it saw even when it recommended nothing. Deliberately NOT in
# _ADVISOR_ROLES (same convention as RETIREMENT_RECOMMENDATION): it must never
# surface in the Overview observations loop.
_RETIREMENT_RUN_SUMMARY_ROLE = "RETIREMENT_RUN_SUMMARY"

# Cycle 2e (AC-7): single-flight guard shared by the scheduled 03:45 tick and
# the POST /ai-advisor/retirement/run route. Always acquired NON-blocking --
# a request thread or the scheduler thread never waits on it -- and released
# by the thread that runs the worker, in a finally.
_RETIREMENT_RUN_LOCK = threading.Lock()


def _retirement_internal_error_summary(error_type: str) -> dict:
    """Minimal-but-complete run summary for a run that failed before (or
    instead of) producing one of its own.

    Same key envelope as advisors.retirement_recommender's summary so every
    reader (the panel template, GET /api/retirement-recommendations) can rely
    on the keys being present; unknown counts are None, never a fabricated 0.
    D-1: `error_type` is a bare type(exc).__name__ -- never str(exc).

    "source" is the literal "composer" because _retirement_recommender_tick_
    worker (this helper's only caller) always builds with source="composer":
    a failed run's summary names the source that run was attempting, not
    something this helper detected. If the worker ever builds with another
    source, this literal must follow it.
    """
    return {
        "run_id": uuid.uuid4().hex,
        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "composer",
        "basis_label": None,
        "status": "internal_error",
        "error_type": error_type,
        "roster_size": None,
        "symphonies_with_series": None,
        "symphonies_without_series": [],
        "series_days_min": None,
        "series_days_max": None,
        "lookback_days": None,
        "pairs_evaluated": None,
        "pairs_screened": None,
        "blocked_uncertainty": None,
        "blocked_stress_too_thin": None,
        "blocked_stress_below_threshold": None,
        "blocked_no_candidate": None,
        "recommendations": 0,
        "screen_threshold": None,
        "stress_gate_min_aligned_days": None,
        # Unknown, not "none found": a failed run never claims an empty matrix.
        "pairs": None,
        "series_windows": None,
        "uncertainty_min_aligned_days": None,
    }


def _persist_retirement_run_summary(summary: dict, persisted_count: int) -> None:
    """Persist ONE RETIREMENT_RUN_SUMMARY advisor_observations row (Cycle 2e,
    AC-4). Never raises (D-1: only type(exc).__name__ is logged).

    `recommendations` is overwritten with `persisted_count` -- the number of
    recommendation rows persist_recommendations actually wrote -- so the
    summary can never claim cards that do not exist. Every other key is the
    producer's, verbatim. Callers invoke this ONLY AFTER the recommendation
    rows are persisted: the Run-now JS polls the summary's run_id and reloads
    the page the moment it changes, so a summary written first could reload
    the page onto a run whose cards are not there yet.
    """
    try:
        row = dict(summary)
        row["recommendations"] = persisted_count
        database.insert_advisor_observation(
            advisor_role=_RETIREMENT_RUN_SUMMARY_ROLE,
            subject_type="fleet",
            subject_id="retirement",
            verdict=row.get("status"),
            raw_response=row,
        )
    except Exception as exc:
        _daemon_log.error("Retirement run summary persist failed: %s", type(exc).__name__)


def _prepare_retirement_recs_for_persist(recs: list[dict], *, written_on: str) -> None:
    """Complete each recommendation in `recs` in place before it is persisted.

    Per recommendation: resolve candidate_name/sibling_name from live
    database.load_state() (honest raw-id fallback; only set when absent),
    round numeric evidence to _MATERIAL_CHANGE_ROUND_NDIGITS, then reuse the
    most recent prior explanation for an unchanged pair or ask the explainer.
    A fresh non-empty explanation is dated `written_on`; a reused one keeps the
    day stored on the prior row (None when that row stored none). An explainer
    failure degrades that one recommendation to no explanation and never
    blocks the rest. Only type(exc).__name__ is logged (D-1).
    """
    if not recs:
        return
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

    # ONE role-wide query per call (not one per rec): the
    # {(candidate_id, sibling_id): raw} map every reuse decision looks up.
    try:
        _prior_rows = database.get_advisor_observations_for_role(
            "RETIREMENT_RECOMMENDATION", limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
        )
        _prior_map = _build_retirement_prior_row_map(_prior_rows)
    except Exception:
        _prior_map = {}

    for rec in recs:
        # Only set when absent, never clobber a value a caller already supplied.
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

        # Round BEFORE the reuse-vs-fresh decision and before the explainer
        # sees it, so the cited precision equals the compared and persisted
        # precision. _round_floats returns a new structure; write it back into
        # the caller's dict.
        rounded = _round_floats(rec, _MATERIAL_CHANGE_ROUND_NDIGITS)
        rec.clear()
        rec.update(rounded)

        reused_explanation = _retirement_find_reusable_prior_explanation(rec, _prior_map)
        if reused_explanation is not None:
            prior_raw = _retirement_prior_raw_for(rec, _prior_map) or {}
            rec["explanation"] = reused_explanation
            rec["explanation_written_on"] = prior_raw.get("explanation_written_on")
            continue
        try:
            # The explainer is not shown what the reuse comparison looks through, so a
            # fresh text cannot cite a figure that a later run would reuse with a stale value.
            shown = {k: v for k, v in rec.items() if k not in _EXPLAINER_WITHHELD_KEYS}
            rec["explanation"] = explain_recommendation(shown)
        except Exception as exc:
            _daemon_log.warning(
                "Retirement explainer failed for %s: %s",
                rec.get("candidate_id"),
                type(exc).__name__,
            )
            rec["explanation"] = None
        rec["explanation_written_on"] = written_on if rec["explanation"] else None


def _retirement_recommender_tick_worker() -> None:
    """Background worker that runs the Retirement Recommender's daily tick.

    Imported lazily to keep advisors.retirement_recommender off the execution
    path (CC-2). Calls the real producer chain --
    build_recommendations_with_summary(source="composer") then
    persist_recommendations(<its own recs>) -- so the read-only
    GET /api/retirement-recommendations route and the AI Advisor panel have
    something to render (PM ruling, review-response cycle 2: without this
    tick nothing in production ever calls the producer, so both read-only
    surfaces render an honest-empty state forever).

    Cycle 2e (retirement-visibility AC-3/AC-4): the producer now returns
    (recs, summary) and reads each symphony's realized daily series from
    Composer; this worker (1) stamps summary["run_id"] onto every rec BEFORE
    the enrichment/explain pass (so the persisted dict is exactly what the
    explainer saw, and so the panel can scope its cards to the latest run),
    (2) persists the recs -- persist_recommendations is called exactly once
    per successful build, even for an empty list -- and (3) ONLY THEN persists exactly one
    RETIREMENT_RUN_SUMMARY row whose `recommendations` is the count actually
    persisted. A run is therefore always visible, even when it recommends
    nothing or fails: a producer exception, a recommendation-persist failure
    or any unexpected pipeline exception still yields one summary row with
    status "internal_error" + the exception's TYPE NAME (D-1, never
    str(exc)); a failure to write the summary itself is swallowed. The same
    worker serves the scheduled 03:45 tick and the manual Run-now route.

    Cycle 2b (AC-2): each recommendation is run through advisors.
    retirement_explainer.explain_recommendation and the result is stamped
    onto rec["explanation"] -- this keeps the LLM entirely out of
    advisors/retirement_recommender.py (kept LLM-free by an AST import scan
    in tests/app/test_retirement_producer_explainer_wiring.py, which replaced
    the Cycle-2b golden-hash pin in Cycle 2e AC-9) and out of the
    render/approve/checklist paths (operator ruling, Gate-2b). A per-rec
    explainer failure degrades that one recommendation's explanation to None
    and never blocks persistence of the rest of the batch.

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

    Tenure-review merge: the enrichment / rounding / reuse-or-explain pass
    that the Cycle 2b/2c/2d paragraphs above describe is implemented ONCE,
    in _prepare_retirement_recs_for_persist, and called both here and by the
    tenure review job (_tenure_review_job). It additionally stamps each rec's
    explanation_written_on (the day a fresh explanation was written, or the
    prior row's day for a reused one), and shows the explainer a copy of the
    rec without _EXPLAINER_WITHHELD_KEYS (run_id among them).

    D-1 error contract: only type(exc).__name__ appears in log records at
    WARNING+ -- a producer failure must never crash the scheduler thread.
    """
    summary: dict | None = None
    persisted_count = 0
    # Type name of an unexpected failure AFTER a successful build (set by the
    # two except clauses below). A failed build is already represented by its
    # own internal_error summary, so it never sets this.
    failure_type: str | None = None
    build_failed = False
    try:
        from advisors.retirement_recommender import (  # lazy — not module-level (CC-2)
            build_recommendations_with_summary,
            persist_recommendations,
        )

        try:
            recs, summary = build_recommendations_with_summary(source="composer")
            if not isinstance(recs, list) or not isinstance(summary, dict):
                raise TypeError("unexpected build_recommendations_with_summary result shape")
        except Exception as exc:
            _daemon_log.error("Retirement recommender build failed: %s", type(exc).__name__)
            build_failed = True
            recs, summary = [], _retirement_internal_error_summary(type(exc).__name__)

        # Cycle 2e (AC-4): stamp the run's id onto every rec BEFORE the
        # enrichment/rounding/reuse/explain pass below, so the dict that gets
        # persisted is exactly the dict the explainer saw.
        if not summary.get("run_id"):
            summary["run_id"] = uuid.uuid4().hex
        for rec in recs:
            rec["run_id"] = summary["run_id"]

        if recs:
            _prepare_retirement_recs_for_persist(
                recs, written_on=datetime.now(_ET).date().isoformat()
            )
        if not build_failed:
            try:
                _persisted = persist_recommendations(recs)
                # persist_recommendations is the authority on how many rows
                # were actually written; anything but a plain int counts as 0.
                if isinstance(_persisted, int) and not isinstance(_persisted, bool):
                    persisted_count = _persisted
                _daemon_log.info(
                    "Retirement recommender tick complete: %d recommendation(s).", len(recs)
                )
            except Exception as exc:
                _daemon_log.error(
                    "Retirement recommendation persist failed: %s", type(exc).__name__
                )
                failure_type = type(exc).__name__
    except Exception as exc:
        _daemon_log.error("Retirement recommender tick worker failed: %s", type(exc).__name__)
        failure_type = type(exc).__name__

    # Cycle 2e (AC-4): exactly ONE summary row per run, written only AFTER the
    # recommendation rows. A run that failed after a successful build must not
    # read "ok" -- its recs were lost -- so the summary is downgraded to an
    # honest internal_error carrying the exception TYPE NAME only (D-1).
    if summary is None:
        summary = _retirement_internal_error_summary(failure_type or "UnknownError")
    elif failure_type is not None and summary.get("status") != "internal_error":
        summary["status"] = "internal_error"
        summary["error_type"] = failure_type
    _persist_retirement_run_summary(summary, persisted_count)


_TENURE_REVIEW_THRESHOLD_KEY = "TENURE_REVIEW_MIN_TRADING_DAYS"
_TENURE_REVIEW_ACCOUNT_KEYS = ("ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD")
_TENURE_REVIEW_FAILED_TEXT = "review failed"


def _tenure_review_settings() -> dict:
    """The review's settings, read fresh from the env file (the file wins over os.environ)."""
    file_values = dotenv_values(ENV_FILE_PATH)
    env = {}
    for key in (_TENURE_REVIEW_THRESHOLD_KEY, *_TENURE_REVIEW_ACCOUNT_KEYS):
        value = file_values.get(key)
        if value is None:
            value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _plain_number(value: object, *, decimals: int) -> str | None:
    """value rounded to decimals places with trailing zeros after the point stripped, or None
    when it is not a finite real number (a bool is not a number)."""
    if not _is_finite_number(value):
        return None
    if isinstance(value, int):
        return str(value)  # an int needs no rounding, and a huge one does not fit a float
    text = f"{round(value, decimals):.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _whole_number_or_none(value: object) -> int | None:
    """value as an int when it is an int or a finite float with no fraction (never a bool)."""
    return int(value) if _is_whole_number(value) else None


def _copy_tenure_threshold(tenure_review: dict | None) -> str:
    """The tenure threshold in trading days: the stored value in effect when it is a whole
    number, else the configured one parsed as the review parses it. Never None."""
    stored = (
        _whole_number_or_none(tenure_review.get("threshold_in_effect"))
        if isinstance(tenure_review, dict)
        else None
    )
    if stored is not None:
        return str(stored)
    from advisors import tenure_review as _tenure_module

    configured = _tenure_review_settings().get(_TENURE_REVIEW_THRESHOLD_KEY)
    return str(_tenure_module.parse_threshold(configured).value)


def _copy_screen_threshold(retirement_last_run: dict | None) -> str | None:
    """The correlation screen threshold the last ok run used, else the module constant.
    None when the constant is not a finite number (the phrase then reads its fallback)."""
    if isinstance(retirement_last_run, dict) and retirement_last_run.get("status") == "ok":
        run_threshold = retirement_last_run.get("screen_threshold")
        if _is_finite_number(run_threshold):
            return f"{run_threshold}"
    from advisors.retirement_recommender import CORRELATION_SCREEN_THRESHOLD

    # None for a constant that is not a finite real number (the phrase then reads its fallback).
    return _plain_number(CORRELATION_SCREEN_THRESHOLD, decimals=6)


def _copy_stress_fraction_pct() -> str | None:
    """The stress window as a percentage of aligned days, or None when the constant is
    not a finite number."""
    from advisors.retirement_recommender import STRESS_WINDOW_FRACTION

    # The fraction is shown as a percentage of aligned days.
    if _plain_number(STRESS_WINDOW_FRACTION, decimals=6) is None:
        return None
    return _plain_number(STRESS_WINDOW_FRACTION * 100, decimals=1)


def _copy_watch_trail_pp() -> str | None:
    """The backtest-trail watch threshold in points, or None when it is not a finite number."""
    from advisors.tenure_review import BACKTEST_TRAIL_WATCH_PP

    return _plain_number(BACKTEST_TRAIL_WATCH_PP, decimals=6)


def _copy_friction_pct() -> str | None:
    """The simulated exit friction in percent, or None when it is not a finite number."""
    from autotuner import SIM_EXIT_FRICTION_PCT

    return _plain_number(SIM_EXIT_FRICTION_PCT, decimals=6)


def _copy_min_history_days() -> str | None:
    """The minimum history days the performance rule needs, or None when the constant is
    not a finite number."""
    # Read at call time: the page prints the constant the insufficient-history rule uses.
    return _plain_number(_PERFORMANCE_MIN_HISTORY_DAYS, decimals=0)


def _copy_shadow_retention_days() -> str:
    """The shadow history retention in days as the nightly prune reads it. Never None."""
    # Read as the nightly prune reads it: the settings file only, default 180 days.
    raw = dotenv_values(ENV_FILE_PATH).get("SHADOW_HISTORY_RETENTION_DAYS", "180")
    return str(int(raw))


def _page_copy_values(
    slots: Iterable[str],
    *,
    tenure_review: dict | None = None,
    retirement_last_run: dict | None = None,
) -> dict:
    """The requested page-copy slots as display strings, each from its own authority.

    A slot whose authority fails is None, so one failure never changes another slot."""
    resolvers = {
        "tenure_threshold": lambda: _copy_tenure_threshold(tenure_review),
        "screen_threshold": lambda: _copy_screen_threshold(retirement_last_run),
        "stress_fraction_pct": _copy_stress_fraction_pct,
        "watch_trail_pp": _copy_watch_trail_pp,
        "friction_pct": _copy_friction_pct,
        "min_history_days": _copy_min_history_days,
        "shadow_retention_days": _copy_shadow_retention_days,
    }
    values: dict = {}
    for slot in slots:
        try:
            values[slot] = resolvers[slot]()
        except Exception:
            values[slot] = None
    return values


def _eastern_stamp(moment: datetime) -> str:
    """moment as "YYYY-MM-DD HH:MM" US Eastern; the one builder of that text."""
    return moment.astimezone(_ET).strftime("%Y-%m-%d %H:%M")


def _log_tenure_review_end(
    trigger: str, run_id: str, ending: str, recommendations: int | None = None
) -> None:
    """The run's completion line: trigger, US Eastern time, run id and how the run ended.

    A completed run also names how many recommendation rows it wrote.
    """
    count = "" if recommendations is None else f" recommendations={recommendations}"
    _daemon_log.info(
        "Tenure review end: trigger=%s at=%s run_id=%s %s%s",
        trigger,
        f"{_eastern_stamp(datetime.now(_ET))} ET",
        run_id,
        ending,
        count,
    )


def _tenure_review_job(trigger: str, owner: object | None = None) -> None:
    """Run one symphony tenure review under the run slot and persist what it found.

    `owner` is the token from the caller's REVIEW_RUN_STATE.try_start; with none,
    the job claims the slot itself and returns doing nothing when it is taken.
    Recommendation rows are written before the run row and never for a run that
    did not complete. A failure after they were written (the run row's write)
    leaves them stored; each carries the review_run_id, and the readers ignore
    the rows of a run that no run row describes. Never raises; only
    type(exc).__name__ is logged and recorded.
    """
    tenure_review: types.ModuleType | None = None
    run_id: str | None = None
    try:
        from advisors import tenure_review  # lazy (CC-2)

        if owner is None:
            owner = tenure_review.REVIEW_RUN_STATE.try_start(trigger)
            if not owner:
                _daemon_log.info(
                    "Tenure review (%s) skipped: a review is already running.", trigger
                )
                return
        from advisors import tenure_review_runner  # lazy (CC-2)

        now_et = datetime.now(_ET)
        run_id = uuid.uuid4().hex
        _daemon_log.info(
            "Tenure review start: trigger=%s at=%s run_id=%s",
            trigger,
            f"{_eastern_stamp(now_et)} ET",
            run_id,
        )
        env = _tenure_review_settings()
        outcome = tenure_review_runner.run_tenure_review(
            account_ids=tenure_review_runner.configured_account_ids(env),
            threshold_raw=env.get(_TENURE_REVIEW_THRESHOLD_KEY),
            now_et=now_et,
            run_id=run_id,
            trigger=trigger,
            account_labels=tenure_review_runner.configured_account_labels(env),
        )
        if not outcome.completed:
            reason = outcome.failure_reason or _TENURE_REVIEW_FAILED_TEXT
            _daemon_log.warning("Tenure review did not complete: %s", reason)
            _log_tenure_review_end(trigger, run_id, "did not complete")
            tenure_review.REVIEW_RUN_STATE.finish_failure(owner, reason, datetime.now(_ET))
            return
        result = outcome.result
        recs = result.recommendations
        for rec in recs:
            rec["review_run_id"] = run_id
        _prepare_retirement_recs_for_persist(recs, written_on=now_et.date().isoformat())
        from advisors.retirement_recommender import persist_recommendations  # lazy (CC-2)

        persist_recommendations(recs)
        for rec in recs:
            for entry in result.entries:
                if entry.get("symphony_id") == rec.get("candidate_id"):
                    entry["explanation"] = rec.get("explanation")
                    entry["explanation_written_on"] = rec.get("explanation_written_on")
        database.insert_advisor_observation(
            advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
            subject_type="tenure_review_run",
            subject_id=run_id,
            verdict="completed",
            raw_response={
                **tenure_review.run_record(result, datetime.now(_ET)),
                "degraded": bool(outcome.degraded),
            },
        )
        tenure_review.REVIEW_RUN_STATE.finish_success(owner)
        _log_tenure_review_end(trigger, run_id, "completed", len(recs))
    except Exception as exc:
        _daemon_log.error("Tenure review failed: %s", type(exc).__name__)
        if run_id is not None:
            _log_tenure_review_end(trigger, run_id, f"failed {type(exc).__name__}")
        if tenure_review is not None and owner:
            tenure_review.REVIEW_RUN_STATE.finish_failure(
                owner, type(exc).__name__, datetime.now(_ET)
            )
    finally:
        if tenure_review is not None and owner:
            tenure_review.REVIEW_RUN_STATE.release(owner)


def _nightly_review_already_done(tenure_review: types.ModuleType) -> bool:
    """True when the newest nightly run is not degraded and covers the as-of day now and
    the threshold now in effect. A stored run that cannot be read, or a failed read or
    parse, means the review should run. Never raises."""
    try:
        as_of = tenure_review.as_of_trading_day(datetime.now(_ET)).isoformat()
        threshold = tenure_review.parse_threshold(
            _tenure_review_settings().get(_TENURE_REVIEW_THRESHOLD_KEY)
        ).value
        rows = database.get_advisor_observations_for_role(
            tenure_review.TENURE_REVIEW_RUN_ROLE, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
        )
        for row in rows:
            raw = row.get("raw_response")
            if isinstance(raw, dict) and raw.get("trigger") == "nightly":
                return (
                    raw.get("degraded") is not True
                    and raw.get("as_of_trading_day") == as_of
                    and raw.get("threshold_in_effect") == threshold
                )
        return False
    except Exception:
        return False


def _retirement_scheduled_tick_body() -> None:
    """Thread body of the scheduled 03:45 retirement tick (Cycle 2e AC-7).

    The single-flight decision lives HERE, inside the daemon thread, never in
    _run_retirement_recommender_tick: the scheduler thread must never decide
    synchronously (it always just spawns). If a run is already in flight (an
    operator's Run-now click) the scheduled tick logs an INFO line and skips
    -- the in-flight run is the same worker and persists the same summary --
    otherwise it runs the worker and releases the lock in a finally.
    """
    if not _RETIREMENT_RUN_LOCK.acquire(blocking=False):
        _daemon_log.info("Retirement recommender tick skipped: a run is already in flight.")
        return
    try:
        _retirement_recommender_tick_worker()
    finally:
        _RETIREMENT_RUN_LOCK.release()


def _retirement_run_now_body() -> None:
    """Thread body of a manual Run-now (Cycle 2e AC-7). The request thread
    already acquired _RETIREMENT_RUN_LOCK (non-blocking) before spawning this
    thread, so this only runs the shared worker and releases the lock in a
    finally -- on a worker failure as well as on success."""
    try:
        _retirement_recommender_tick_worker()
    finally:
        _RETIREMENT_RUN_LOCK.release()


def _retirement_nightly_tick_body(owner: object) -> None:
    """Thread body of the scheduled 03:45 tick: the tenure review, then the
    recommender run.

    `owner` is the REVIEW_RUN_STATE token the tick wrapper claimed before it
    built the thread. Stage 1 is the symphony tenure review (_tenure_review_job,
    which releases that token). Stage 2 is the Cycle 2e recommender run
    (_retirement_scheduled_tick_body), whose single-flight decision against a
    concurrent Run-now lives inside it, never in the scheduler thread. The
    stages are independent: neither raises (D-1), and stage 2 still runs when
    stage 1 did not complete, so a Composer outage that fails the review still
    leaves the recommender run's own summary row.
    """
    try:
        _tenure_review_job("nightly", owner)
    finally:
        _retirement_scheduled_tick_body()


def _run_retirement_recommender_tick() -> None:
    """Non-blocking daily off-hours wrapper for the nightly retirement tick.

    The tick starts the symphony tenure review and, in the same daemon thread
    right after it, the Cycle 2e recommender run. It starts NOTHING (and writes
    nothing) when the newest completed nightly review already covers the as-of
    day at the threshold in effect (a weekend or holiday tick would repeat a full
    review of the same session) or when a review is already running; both
    cases log one INFO line.

    The review slot is claimed BEFORE the thread is built, so a second tick or
    a request in that window is refused; a thread that cannot be built or
    started gives the slot back, and the scheduler loop never sees an
    exception. The scheduler thread returns immediately -- the tick never
    blocks the 1-minute execution path (arch constraint 1) -- and the
    recommender stage's single-flight check against a concurrent manual
    Run-now happens inside the thread (_retirement_scheduled_tick_body).
    """
    owner = None
    tenure_review = None
    try:
        from advisors import tenure_review  # lazy (CC-2)

        if _nightly_review_already_done(tenure_review):
            _daemon_log.info(
                "Nightly tenure review skipped: the as-of day is already reviewed "
                "at the threshold in effect."
            )
            return
        owner = tenure_review.REVIEW_RUN_STATE.try_start("nightly")
        if not owner:
            _daemon_log.info("Nightly tenure review skipped: a review is already running.")
            return
        threading.Thread(
            target=lambda: _retirement_nightly_tick_body(owner),
            daemon=True,
            name="retirement-recommender-tick",
        ).start()
    except Exception as exc:
        _daemon_log.error("Nightly tenure review could not start: %s", type(exc).__name__)
        if owner:
            tenure_review.REVIEW_RUN_STATE.release(owner)


# The results a closed market's polls are answered from are built ahead of the first poll, on a
# background thread: when the daemon starts, and then every _CLOSED_WARMUP_EVERY_S while the market
# is closed (a new snapshot, a newer close or newly taken account totals name a result not held yet;
# a build that finds everything held reads the state and returns). Never on the scheduler's thread.
_CLOSED_WARMUP_EVERY_S = 30
_CLOSED_WARMUP_FLIGHT = threading.Lock()  # one build in flight at a time
_closed_warmup_checked_mono: float | None = None
# The picker's other windows, built after the default one (the one the page opens on).
_WARMUP_OTHER_WINDOWS = ("60d", "90d", "125d", "ytd", "1y", "all")


def _warm_closed_results() -> None:
    """Build the held results of the market's closed session: the poll and the page, and every
    window's strip and hero chart. Builds nothing while the market is open, or when no close
    snapshot (stored or built from the last committed state) exists. Never raises."""
    if not _CLOSED_WARMUP_FLIGHT.acquire(blocking=False):
        return
    try:
        now_et = datetime.now(_ET)
        market_state = get_market_state(now_et)
        if market_state == "open":
            return
        state = database.load_state() or {}
        trading_session = _session_view(state, market_state, now_et)
        if trading_session.snapshot is None:
            return
        conn = _committed_shadow_conn(trading_session.cut_state)
        token_of_conn = _WARMUP_CONN.set(conn)
        try:
            with analytics.request_reads():
                _closed_poll_view(trading_session, state, now_et)
                for token in (_DEFAULT_HERO_WINDOW, *_WARMUP_OTHER_WINDOWS):
                    _windowed_strip(trading_session, token, state, now_et)
                    _closed_hero_body(trading_session, token, now_et)
        finally:
            _WARMUP_CONN.reset(token_of_conn)
            conn.close()
    except Exception as exc:
        _daemon_log.warning("closed-market warm-up failed: %s", type(exc).__name__)
    finally:
        _CLOSED_WARMUP_FLIGHT.release()


def _start_closed_warmup() -> None:
    """Run _warm_closed_results on a daemon thread; returns at once. Never raises."""
    try:
        threading.Thread(target=_warm_closed_results, daemon=True, name="closed-warmup").start()
    except Exception as exc:
        _daemon_log.warning("closed-market warm-up not started: %s", type(exc).__name__)


def _poke_closed_warmup() -> None:
    """Start the warm-up at most once per _CLOSED_WARMUP_EVERY_S; called by every scheduler pass."""
    global _closed_warmup_checked_mono
    now_mono = _monotonic()
    last = _closed_warmup_checked_mono
    if last is not None and now_mono - last < _CLOSED_WARMUP_EVERY_S:
        return
    _closed_warmup_checked_mono = now_mono
    _start_closed_warmup()


def run_scheduler():
    global _scheduler_started_mono, _closed_warmup_checked_mono
    _scheduler_started_mono = _monotonic()
    # The first poll after a daemon start is answered from a result built beforehand.
    _closed_warmup_checked_mono = _scheduler_started_mono
    _start_closed_warmup()
    _guard(schedule.every().minute.at(":00").do(threaded_trigger), ":00 engine spawn")
    # The refresh runs on its own thread so a held Composer call never delays the spawn.
    _guard(
        schedule.every().minute.at(":00").do(_start_account_refresh, "minute"),
        ":00 account refresh",
    )
    _guard(schedule.every().day.at("02:00").do(_run_trigger_retention), "02:00 trigger retention")
    # Component 7+8: daily off-hours lens pipeline — Market Prism summary (CYCLE4-BRIEF.md).
    # Runs at 03:00 (off-hours) so it never overlaps the live market-hours execution path.
    _guard(schedule.every().day.at("03:00").do(_run_lens_pipeline), "03:00 lens pipeline")
    # Strategy Incubation Gate daily tick — staggered 30 minutes after the lens
    # pipeline slot so the two off-hours jobs never contend for the same minute.
    _guard(schedule.every().day.at("03:30").do(_run_incubation_tick), "03:30 incubation tick")
    # Retirement Recommender daily tick — staggered 15 minutes after the
    # incubation slot so all three off-hours jobs run without same-minute
    # contention.
    _guard(
        schedule.every().day.at("03:45").do(_run_retirement_recommender_tick),
        "03:45 retirement recommender",
    )
    # Daily re-list (only when the registry is stale) so new model releases reach the badges.
    _guard(schedule.every().day.at("04:15").do(_start_model_warmup), "04:15 model warmup")
    # Regime label job: after the warm-up, outside every session in UTC and New York host time.
    _guard(schedule.every().day.at("04:30").do(_run_regime_label_tick), "04:30 regime label job")
    while True:
        _scheduler_tick()
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


def _build_sleeves_panel_context(
    *, raise_on_read_failure: bool = False, session_day: date | None = None
) -> list[dict]:
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

    A failed read of the sleeves table answers an empty list, which reads as "no
    sleeves"; a caller that must tell the two apart (the state poll's panels) passes
    ``raise_on_read_failure=True`` and gets the exception instead.

    ``session_day`` is the ET session whose fires the "today" count states (the session a closed
    page shows); omitted, it is today's ET date.
    """
    from sleeves import ledger as sleeve_ledger  # noqa: PLC0415
    from sleeves.rules import limits as sleeve_limits  # noqa: PLC0415

    panel_sleeves: list[dict] = []
    try:
        sleeve_rows = database.get_all_sleeves()
    except Exception:
        if raise_on_read_failure:
            raise
        return panel_sleeves

    now_utc = datetime.now(UTC)
    today_str = (session_day or datetime.now(_ET).date()).isoformat()
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


def _is_plain_number(value) -> bool:
    """A finite int or float (never a bool): the numbers the page prints."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _js_dollars(value, missing: str) -> str:
    """``$12.34`` as the page's script prints a dollar figure (toFixed rounding), else ``missing``.
    The server render and the state poll print one text."""
    return "$" + analytics.js_to_fixed(value, 2) if _is_plain_number(value) else missing


def _sleeves_for_render(sleeves: list[dict]) -> list[dict]:
    """The Sleeves rows with the text the panel prints for each figure."""
    shown = []
    for sleeve in sleeves:
        rules = [
            {**rule, "realized_text": _js_dollars(rule.get("realized_pnl_usd"), "n/a")}
            for rule in sleeve.get("rules") or []
        ]
        shown.append(
            {
                **sleeve,
                "capital_text": _js_dollars(sleeve.get("capital_usd"), "\u2014"),
                "ledger_cash_text": _js_dollars(sleeve.get("ledger_cash_usd"), "\u2014"),
                "realized_text": _js_dollars(sleeve.get("realized_pnl_usd"), "n/a"),
                "rules": rules,
            }
        )
    return shown


def _cvar_for_render(diagnostic: dict | None) -> dict:
    """The text the CVaR panel prints for a diagnostic row (a dash for each missing figure)."""
    row = diagnostic if isinstance(diagnostic, dict) else {}
    five = row.get("cvar_5pct")
    stderr = row.get("cvar_5pct_stderr")
    n_tail = row.get("cvar_n_tail")
    if isinstance(n_tail, float) and n_tail.is_integer():
        n_tail = int(n_tail)
    return {
        "five": analytics.js_to_fixed(five, 4) if _is_plain_number(five) else "\u2014",
        "show_stderr": _is_plain_number(five),
        "stderr": "\u00b1"
        + (analytics.js_to_fixed(stderr, 4) if _is_plain_number(stderr) else "\u2014")
        + " stderr",
        "stderr_attr": str(stderr) if _is_plain_number(stderr) else "",
        "n_tail": "n=" + (str(n_tail) if _is_plain_number(n_tail) else "\u2014"),
        "n_tail_attr": str(n_tail) if _is_plain_number(n_tail) else "",
    }


def _mdd_lifetime_text(value) -> str:
    """A card's lifetime max drawdown as the page prints it: the magnitude to one place, ``--`` when
    there is none."""
    return analytics.js_to_fixed(abs(value), 1) + "%" if _is_plain_number(value) else "--"


def _panels_unreadable() -> dict:
    """What the poll carries for the panels when their builder itself fails: every part reads as
    "could not read", so the page keeps what it shows."""
    return {"sleeves": None, "cvar": {"ok": False, "diagnostic": None}, "vars_locked_count": None}


def _dashboard_panels_payload(bot_state: dict, session_day: date | None = None) -> dict:
    """The panels of ``/`` that the page render and the poll both print, read from the database.

    ``sleeves`` is the Managed Sleeves rows (``None`` when the read failed, ``[]`` for no sleeves),
    ``cvar`` the latest CVaR diagnostic of the first symphony (``ok`` false when the read failed,
    ``diagnostic`` None when there is none yet) and ``vars_locked_count`` the locked variables across
    the symphonies (``None`` when the read failed). Each part is read on its own, so one failure
    changes only that part. Reads only: nothing here places, cancels or changes anything.
    """
    state = bot_state if isinstance(bot_state, dict) else {}
    symphony_keys = [k for k, v in state.items() if _is_symphony_state_entry(v)]

    try:
        sleeves = _build_sleeves_panel_context(raise_on_read_failure=True, session_day=session_day)
    except Exception:
        sleeves = None

    # Phase-1 scope: only the first symphony's diagnostic is shown (CVAR-001).
    cvar: dict = {"ok": True, "diagnostic": None}
    try:
        if symphony_keys:
            row = database.read_cvar_diagnostic_for_symphony(symphony_keys[0])
            if isinstance(row, dict):
                cvar["diagnostic"] = row
    except Exception:
        cvar = {"ok": False, "diagnostic": None}

    # One read for every symphony: the poll must not open a connection per symphony.
    try:
        names = [database.normalize_name(state[key].get("name", "")) for key in symphony_keys]
        counts = database.get_locked_var_counts(names)
        if isinstance(counts, dict):
            vars_locked_count: int | None = sum(counts.get(name, 0) for name in names)
        else:
            # A database that does not answer the bulk read with a mapping: ask for each
            # symphony's strategy.
            locked = 0
            for name in names:
                strategy = database.get_symphony_strategy(name)
                if isinstance(strategy, dict):
                    locked += len(strategy.get("locked_vars") or [])
            vars_locked_count = locked
    except Exception:
        vars_locked_count = None

    return {"sleeves": sleeves, "cvar": cvar, "vars_locked_count": vars_locked_count}


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
def _inject_guard_alpha(*entry_maps: dict) -> None:
    """Stamp guard_alpha onto triggered symphony entries (keyed by id) for card verdicts.

    Several maps share one read of the exit rows.

    Shared by GET / (first paint) and get_state (poll) so both carry one number.
    setdefault keeps a value already on the entry; any failure leaves verdicts at the dash.
    An entry with no current_return gets no figure: an absent return is not a 0.0 return.
    """
    try:
        triggered_ids = list(
            {
                k
                for entries in entry_maps
                for k, v in entries.items()
                if isinstance(v, dict) and v.get("triggered")
            }
        )
        if not triggered_ids:
            return
        for sid, ga in database.get_guard_alpha_by_symphony(triggered_ids).items():
            for entries in entry_maps:
                if sid in entries and isinstance(entries[sid], dict):
                    cr = entries[sid].get("current_return")
                    if cr is None:
                        continue
                    entries[sid].setdefault("guard_alpha", round(float(ga) - float(cr), 6))
    except Exception:
        pass


def _active_fleet_alert() -> dict | None:
    """The undismissed fleet alert row, or None (also for a non-dict or an unreadable table)."""
    try:
        row = database.read_fleet_alert()
    except Exception:
        return None
    if isinstance(row, dict) and row.get("dismissed_at_et") is None:
        return row
    return None


_DIAL_DASH = "—"
_DIAL_EMPTY_OFFSET = "94.25"  # circumference of the r=15 dial arc, 2*pi*15, in SVG user units
_DIAL_SENTINEL_ABS = 900  # engine uses +-999 as "not set"; the client treats abs >= 900 as unset
_DIAL_WARN_BELOW_PCT = 15  # arc turns warn colour below this reading (percent)
_DIAL_DIM_ABOVE_PCT = 80  # arc turns dim above this reading (percent)


def _first_paint_verdict(sym: dict) -> dict:
    """Verdict text for a triggered card, worded as static/index.js writes it on a poll."""
    ga = sym.get("guard_alpha")
    if (
        isinstance(ga, bool)
        or not isinstance(ga, (int, float))
        or not math.isfinite(ga)
        or abs(ga) >= analytics.JS_TO_FIXED_EXPONENT_FROM
    ):
        return {"_verdict_text": _DIAL_DASH, "_verdict_has_value": False}
    magnitude = analytics.js_to_fixed(abs(ga), 1)
    if magnitude == "0.0":
        text = "Exit matched holding"
    elif ga > 0:
        text = f"Exit beat holding by {magnitude} pts"
    else:
        text = f"Exit trailed holding by {magnitude} pts"
    return {"_verdict_text": text, "_verdict_has_value": True}


def _first_paint_dial(sym: dict) -> dict:
    """Dial arc offset, label and colour token, computed as static/index.js renderMcDial does."""
    m = sym.get("mc_prob")
    unknown = (
        sym.get("triggered")
        or isinstance(m, bool)
        or not isinstance(m, (int, float))
        or not math.isfinite(m)
        or abs(m) >= _DIAL_SENTINEL_ABS
    )
    if unknown:
        return {
            "_dial_offset": _DIAL_EMPTY_OFFSET,
            "_dial_text": _DIAL_DASH,
            "_dial_token": "--studio-ink-faint",
        }
    pct = max(0, min(100, m)) / 100
    if m < _DIAL_WARN_BELOW_PCT:
        token = "--studio-warn"
    elif m > _DIAL_DIM_ABOVE_PCT:
        token = "--studio-ink-dim"
    else:
        token = "--studio-accent"
    return {
        "_dial_offset": analytics.js_to_fixed(float(_DIAL_EMPTY_OFFSET) * (1 - pct), 2),
        "_dial_text": analytics.js_to_fixed(pct * 100, 0) + "%",
        "_dial_token": token,
    }


def _js_count(value: int | float | str | None) -> str:
    """A count as the client's string concatenation prints it ('?' when absent)."""
    if value is None:
        return "?"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fleet_alert_sentence(alert: dict) -> str:
    """Banner sentence for a fleet alert, worded as templates/index.html updateBanners does."""
    reason = re.sub(
        r"\b\w",
        lambda m: m.group().upper(),
        str(alert.get("triggered_reason") or "exit").replace("_", " "),
        flags=re.ASCII,
    )
    tripped, active = alert.get("tripped_count"), alert.get("active_count")
    pct = ""
    if (
        tripped
        and active
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (tripped, active))
    ):
        pct = f" ({analytics.js_to_fixed(tripped / active * 100, 0)}% of the fleet)"
    at = alert.get("tripped_at_et")
    trip_time = f". Tripped {str(at)[11:16]} ET." if at else "."
    return (
        f"Concentrated risk — {_js_count(tripped)} of {_js_count(active)} "
        f"symphonies exited on {reason} within minutes{pct}{trip_time}"
    )


def _shares_reads(view_function):
    """Run a route under analytics.request_reads(): the per-symphony reads its figures repeat (the
    cards, the rows and the windowed strip each ask for the same symphony's trajectory) are made
    once per request."""

    @functools.wraps(view_function)
    def wrapper(*args, **kwargs):
        with analytics.request_reads():
            return view_function(*args, **kwargs)

    return wrapper


@app.route("/")
@_shares_reads
def dashboard():
    api_state = get_api_state_dict()
    live_state = api_state.get("bot_state") or database.load_state()
    now_et = datetime.now(_ET)
    trading_session = _session_view(live_state, get_market_state(now_et), now_et)
    # The page renders from the same single computation the poll serves: after the close
    # that is the close snapshot (held per snapshot, so the page reads no rows), not the live
    # state that has moved past it.
    if trading_session.snapshot is not None:
        return _render_dashboard(api_state, live_state, trading_session, now_et, None)
    shadow_conn = _committed_shadow_conn(trading_session.cut_state)
    try:
        return _render_dashboard(api_state, live_state, trading_session, now_et, shadow_conn)
    finally:
        shadow_conn.close()


def _render_dashboard(
    api_state: dict,
    live_state: dict,
    trading_session: "_SessionView",
    now_et: datetime,
    shadow_conn: sqlite3.Connection | None,
):
    """Render the page for *session*; *shadow_conn* is None for a closed market's held view."""
    market_state = trading_session.market_state
    if shadow_conn is None:
        view, portfolio_strip = _closed_poll_view(trading_session, live_state, now_et)
    else:
        view = _portfolio_view(live_state, trading_session, shadow_conn, now_et)
        portfolio_strip = _strip_with_hist_fallback(
            view["portfolio_strip"], shadow_conn, live_state
        )
    bot_state = view["state"] or {}
    if view["branch"] == "frozen":
        # Before the defaults fill a missing current_return with 0.0.
        _inject_guard_alpha(bot_state)
        _normalize_frozen_symphonies(view["snapshot"])
    meta = _build_meta(
        bot_state,
        market_state=market_state,
        portfolio_strip=portfolio_strip,
        window_anchor=trading_session.anchor,
        session_wording_day=trading_session.wording_day,
    )
    portfolio_strip = view["portfolio_strip"]

    # The same read the state poll carries (``dashboard_panels``), so the render and the poll agree.
    try:
        panels = _dashboard_panels_payload(
            _without_symphonies(live_state, view.get("no_longer_held", frozenset())),
            trading_session.anchor,
        )
    except Exception:
        panels = _panels_unreadable()
    vars_locked_count = panels["vars_locked_count"] or 0

    # Partition symphonies into active (armed/triggered) and standby.
    symphonies = api_state.get("symphonies") or [
        v for v in bot_state.values() if isinstance(v, dict) and "name" in v
    ]
    # Ensure every symphony dict carries its own key as 'id' so Jinja data-sym-id renders correctly.
    for _sym_key, _sym_val in bot_state.items():
        if isinstance(_sym_val, dict) and "name" in _sym_val and not _sym_val.get("id"):
            _sym_val["id"] = _sym_key

    # FIX-23: Enrich each symphony with _cr/_tc/_mdd from analytics so cards render live returns.
    _dash_today = trading_session.anchor.isoformat()
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
                analytics.get_symphony_cumulative_return,
                _sym_dict,
                _s,
                trading_day=_dash_today,
                conn=shadow_conn,
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
                conn=shadow_conn,
                coerce_none=False,
            )
        if "_mdd" not in _s:
            # DE-PERF-WINDOW-TRUTH-001 (mdd-review BLOCK finding, 2026-09-03):
            # same bug class as F-016 above -- _mdd feeds the per-card Bot/
            # Held/Lifetime MDD cells (templates/index.html), which already
            # have their own None-aware `is not none` guards. Coercing a
            # genuine None (thin shadow-history <2 days, or no Composer
            # max_drawdown scalar) to 0.0 here fabricated a false "0.0%"
            # before the template's correct guard ever saw it.
            _s["_mdd"] = _safe_analytics(
                analytics.get_symphony_max_drawdown,
                _sym_dict,
                _s,
                trading_day=_dash_today,
                conn=shadow_conn,
                coerce_none=False,
            )

    for _s in symphonies:
        if isinstance(_s, dict):
            _s["_mdd_lifetime_text"] = _mdd_lifetime_text(_mdd_lifetime(_s))

    # First paint carries the verdict and dial text the client would write on its
    # first poll, so the card never shows a placeholder before then.
    if view["branch"] != "frozen":
        _inject_guard_alpha(
            bot_state, {s["id"]: s for s in symphonies if isinstance(s, dict) and s.get("id")}
        )
    for _s in symphonies:
        if isinstance(_s, dict):
            _s.update(_first_paint_verdict(_s))
            _s.update(_first_paint_dial(_s))
    _fleet_alert = _active_fleet_alert()

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

    # M2 CVaR diagnostic: the latest row of the first symphony, from the panels read above
    # (CVAR-001 Phase-1 scope limit: the first symphony only). None until the table has a row, and
    # when the read failed.
    cvar_diagnostic = panels["cvar"]["diagnostic"]

    _today_close = market_calendar.session_close(now_et.date())
    session_close_display = _today_close.strftime("%H:%M ET")

    engine_phase = _engine_phase(
        now_et,
        market_state,
        bot_state.get("last_successful_cycle_at"),
    )

    # While the market is not open the figures are the last session's, so the labels name that day
    # ("Oct 30") instead of saying today or live; static/index.js writes the same words on a poll.
    session_day = None
    wording_day = trading_session.wording_day
    if wording_day is not None:
        session_day = f"{wording_day.strftime('%b')} {wording_day.day}"

    return render_template(
        "index.html",
        session_day=session_day,
        engine_phase=engine_phase,
        # The headline's descriptor says "Simulated" while the engine's last cycle placed no real
        # orders; the poll's bot_state carries the same flag (last_execution_mode).
        execution_simulated=_execution_is_simulated(live_state),
        # The figure is the exit return minus the card's current return: live while the
        # market is open, fixed at the capture after the close.
        verdict_basis_label="at close" if session_day else "vs held now",
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
        dashboard_initial_data=_dashboard_initial_data(
            meta,
            bot_state,
            hero_state=view["weights_state"],
            hero=(
                _closed_hero_body(trading_session, _DEFAULT_HERO_WINDOW, now_et)
                if shadow_conn is None
                else None
            ),
            anchor=trading_session.cutoff_anchor,
        ),
        fleet_alert=_fleet_alert,
        fleet_alert_text=_fleet_alert_sentence(_fleet_alert) if _fleet_alert else "",
        sleeves=_sleeves_for_render(panels["sleeves"] or []),
        cvar_view=_cvar_for_render(cvar_diagnostic),
        atlas_cache_health=_atlas_cache_health(),
        copy_values=_page_copy_values(("shadow_retention_days", "friction_pct")),
    )


# The day cap the /api/state poll series carries; the page needs it to judge a fallback series.
def _producer_day_cap(producer) -> int | None:
    """The producer's own ``days`` default when it is a positive int, else None.

    The cap only feeds a cosmetic caption, so a reshaped producer must not break import.
    """
    try:
        default = inspect.signature(producer).parameters["days"].default
    except (KeyError, TypeError, ValueError):
        return None
    if isinstance(default, int) and not isinstance(default, bool) and default > 0:
        return default
    return None


_HERO_POLL_DAY_CAP = _producer_day_cap(
    getattr(analytics, "get_portfolio_bot_and_held_daily_returns", None)
)


def _dashboard_initial_data(
    meta: dict | None,
    bot_state: dict,
    *,
    hero_conn: sqlite3.Connection | None = None,
    hero_state: dict | None = None,
    hero: dict | None = None,
    anchor: date | None = None,
) -> dict:
    """Chart data embedded in the dashboard render so the charts draw at first paint.

    ``hero`` is the default-window series (local shadow history only, no network); a closed
    market passes the held one it shares with ``/api/hero-chart``, otherwise it is read here,
    counting its window back from *anchor*;
    ``hero_poll_tail`` describes the series the next ``/api/state`` poll will carry, so
    the page can tell a changed series from an unchanged one; ``sparks`` carries each
    card's exit-marker fields.
    """
    portfolio = meta.get("portfolio") if isinstance(meta, dict) else None
    portfolio = portfolio if isinstance(portfolio, dict) else {}
    dates = portfolio.get("hist_dates") or []
    bot = portfolio.get("hist_bot") or []
    held = portfolio.get("hist_held") or []
    tail = {"length": len(dates), "last_date": None, "last_bot": None, "last_held": None}
    if dates:
        tail["last_date"] = dates[-1]
        tail["last_bot"] = bot[-1] if bot else None
        tail["last_held"] = held[-1] if held else None
    sparks = {
        (v.get("id") or key): {
            "triggered": bool(v.get("triggered")),
            "triggered_at_time": v.get("triggered_at_time"),
            "triggered_reason": v.get("triggered_reason"),
        }
        for key, v in bot_state.items()
        if isinstance(v, dict) and "name" in v
    }
    return {
        "hero": (
            hero
            if hero is not None
            else _hero_chart_body(_DEFAULT_HERO_WINDOW, hero_conn, hero_state, anchor)
        ),
        "hero_poll_tail": tail,
        "hero_poll_cap": _HERO_POLL_DAY_CAP,
        "sparks": sparks,
        # The stamp the render prints beside the hero chart: the page compares the first
        # poll's stamp with it to tell whether the poll carries newer data.
        "data_stamp": portfolio.get("data_as_of"),
    }


_MARKET_STATE_UNSET = object()


def _hero_mdd_row(window_legs: object, lifetime_legs: dict, lifetime_insufficient: bool) -> dict:
    """The hero Max DD row's ``{bot, held, insufficient}``: the default window's legs when the
    strip carries them, else the lifetime pair the strip always had (an older strip)."""
    if isinstance(window_legs, dict):
        n_obs = window_legs.get("n_obs")
        return {
            "bot": window_legs.get("dry_run"),
            "held": window_legs.get("if_held"),
            "insufficient": not (isinstance(n_obs, int) and n_obs >= _MDD_MIN_OBS),
        }
    return {
        "bot": lifetime_legs.get("dry_run"),
        "held": lifetime_legs.get("if_held"),
        "insufficient": lifetime_insufficient,
    }


def _seconds_to_next_minute(now: datetime) -> int:
    """Seconds until the next engine cycle: the minute scheduler fires at :00 of every minute.

    The one source of the countdown for ``/api/state`` and ``/api/header-status``: 60 at second 0,
    otherwise ``int(60 - seconds into the minute)``, never below 0.
    """
    seconds_into_minute = now.second + now.microsecond / 1_000_000
    return max(0, int(60 - seconds_into_minute))


def _build_meta(
    state_data: dict,
    next_run_seconds: int = 0,
    market_state=_MARKET_STATE_UNSET,
    portfolio_strip: dict | None = None,
    window_anchor: date | None = None,
    session_wording_day: date | None = None,
) -> dict:
    """Build the meta object for /api/state responses (AC-C1 Foundation).

    window_anchor: the ET trading day the portfolio figures belong to (_SessionView.anchor); the
    page cuts its own windows at it, never at the browser's clock.
    session_wording_day: the session the page's words name (_SessionView.wording_day), so the
    page script words a poll the way the server worded the first paint.
    """
    # Use module-qualified call so test patches on dotenv.dotenv_values take effect.
    env_vars = _dotenv_module.dotenv_values(ENV_FILE_PATH)
    live_mode = env_flags.is_true(env_vars.get("LIVE_EXECUTION", "False"))

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

    # Build meta.portfolio from portfolio_strip (AC-C2 Hero chart data).
    ps = portfolio_strip or {}
    tc_data = ps.get("today_change") or {}
    cr_data = ps.get("cumulative_return") or {}
    mdd_data = ps.get("max_drawdown") or {}
    _hist_dates = ps.get("hist_dates", [])
    # AC-1 (DE-PERF-WINDOW-TRUTH-001, re-scope per team-lead ruling 2026-09-03):
    # this flag's ONLY consumers are the Max-DD row/badges in
    # templates/index.html (the hero vs-row + both per-symphony card blocks) --
    # it PREVIOUSLY doubled as a BASIS-MISMATCH guard (bot MDD computed from a
    # few days of shadow history vs held MDD from Composer's full-lifetime
    # scalar) on top of its legitimate Bailey/de-Prado (2014)
    # statistical-stability purpose, sourced from _hist_dates -- the UNRELATED
    # hero-CHART series length, never guaranteed to track the MDD computation's
    # own window. That decoupling is exactly the mechanism that let the
    # original F-2 mitigation decay silently once shadow_history crossed 30
    # trading days (see docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md). The basis
    # mismatch itself is now fixed AT THE SOURCE
    # (analytics.get_portfolio_max_drawdown's if_held/dry_run are a genuine
    # same-window pair) -- no basis guard is needed here at all. Only the
    # stat-stability concern survives, re-scoped to read the MDD computation's
    # OWN n_obs (the exact day-count that fed it) instead of _hist_dates.
    _mdd_n_obs = mdd_data.get("n_obs") or 0
    _insufficient_history = _mdd_n_obs < _MDD_MIN_OBS
    portfolio_meta = {
        "tc": tc_data.get("dry_run"),
        "tc_if_held": tc_data.get("if_held"),
        "cr": cr_data.get("dry_run"),
        "cr_if_held": cr_data.get("if_held"),
        "mdd": mdd_data.get("dry_run"),
        "mdd_if_held": mdd_data.get("if_held"),
        # AC-2: the Composer lifetime scalar, kept as its OWN clearly-labelled
        # figure (see templates/index.html's "mdd-lifetime-scalar" render) --
        # never a leg of the mdd/mdd_if_held comparison above.
        "mdd_if_held_lifetime": mdd_data.get("if_held_lifetime"),
        "mdd_n_obs": _mdd_n_obs,
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
        # The default window's volatility, which the hero's Ann. Vol row opens on.
        "window_vol_bot": ps.get("window_vol_bot", ps.get("vol_bot")),
        "window_vol_held": ps.get("window_vol_held", ps.get("vol_held")),
        # The hero's Max DD row opens on the default window's legs and states for itself
        # whether that window held enough days (the cards keep the lifetime pair above).
        "mdd_row": _hero_mdd_row(ps.get("window_max_drawdown"), mdd_data, _insufficient_history),
        # Option A: the WINDOWED guard alpha (default window) + its window echo feed the
        # SSR hero headline so first paint matches the picker; account_all_time_cr is the
        # SEPARATE, non-windowed "Account · all-time" reference.
        "guard_alpha": ps.get("guard_alpha"),
        "window": ps.get("window"),
        "account_all_time_cr": ps.get("account_all_time_cr"),
        "window_anchor": window_anchor.isoformat() if window_anchor is not None else None,
    }

    return {
        "mode": "LIVE" if live_mode else "DRY RUN",
        "system_online": market_state == "open",
        "session_wording_day": (
            session_wording_day.isoformat() if session_wording_day is not None else None
        ),
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


def _is_symphony_in_account(value: object, account_filter: str | None) -> bool:
    """True for a symphony state entry (a dict with a name) in the filtered account.

    An empty filter matches every symphony entry; non-symphony dicts never match.
    """
    return _is_symphony_state_entry(value) and (
        not account_filter or value.get("account") == account_filter
    )


_SHADOW_TS_ET_FORMAT = "%Y-%m-%dT%H:%M:%S"  # the form of shadow_history.ts_et


def _cycle_stamp_moment(stamp: object) -> datetime | None:
    """The aware ET instant of a stored ``last_successful_cycle_at`` stamp, or None when it is
    absent or unparseable (a naive stamp is read as ET)."""
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_ET)
    return moment.astimezone(_ET)


def _committed_shadow_conn(state: dict | None) -> sqlite3.Connection:
    """Read-only shadow_history connection that sees only completed engine cycles.

    The engine commits a cycle's shadow rows before it saves bot_state, so the newest
    rows can belong to a cycle whose state is not saved yet. The connection carries a
    temp view named shadow_history (SQLite resolves an unqualified name to the temp
    schema first) limited to rows stamped at or before the state's
    last_successful_cycle_at, so every reader sharing it sees one cycle. An absent or
    unparseable stamp gives a plain read-only connection. Errors
    propagate: a response without its committed view must fail rather than read per-call
    connections.
    """
    conn = sqlite3.connect(
        f"file:{analytics._get_shadow_db_file()}?mode=ro", uri=True, timeout=10.0
    )
    try:
        moment = _cycle_stamp_moment((state or {}).get("last_successful_cycle_at"))
        cutoff = moment.strftime(_SHADOW_TS_ET_FORMAT) if moment is not None else None
        if cutoff is not None:
            conn.execute("CREATE TEMP TABLE shadow_cutoff(ts_et TEXT)")
            conn.execute("INSERT INTO shadow_cutoff(ts_et) VALUES (?)", (cutoff,))
            conn.execute(
                "CREATE TEMP VIEW shadow_history AS SELECT * FROM main.shadow_history "
                "WHERE ts_et <= (SELECT ts_et FROM temp.shadow_cutoff)"
            )
    except BaseException:
        conn.close()
        raise
    return conn


def _execution_is_simulated(state: dict | None) -> bool:
    """True when the engine's last cycle ran simulated (``last_execution_mode`` saved False).

    A state that does not say reads as not simulated: the page then makes no "Simulated" claim.
    """
    return (state or {}).get("last_execution_mode") is False


def _no_longer_held_ids(
    state: dict | None,
    conn: sqlite3.Connection | None = None,
    now_et: datetime | None = None,
) -> frozenset[str]:
    """The ids of the state's symphonies that are no longer held, from the ONE shared rule.

    Every surface that lists, counts or weights symphonies asks ``analytics
    .no_longer_held_symphony_ids`` through here, so no page decides for itself. ``conn`` is the
    request's own connection (the closed-market branch passes its cut); without one the rule
    opens a single read-only connection. A read that fails answers "none": a page keeps showing
    what it has rather than hiding a symphony on an error.
    """
    try:
        ids = analytics.no_longer_held_symphony_ids(state, conn=conn, now=now_et)
    except Exception:
        _daemon_log.debug("no-longer-held read failed", exc_info=True)
        _note_build_failure()
        return frozenset()
    return frozenset(ids) if isinstance(ids, (set, frozenset, list, tuple)) else frozenset()


def _without_symphonies(state: dict, gone: frozenset[str]) -> dict:
    """``state`` without the entries named in ``gone``; the same object when none are named."""
    if not gone:
        return state
    return {key: value for key, value in state.items() if key not in gone}


def _snapshot_without_symphonies(snapshot: dict, gone: frozenset[str]) -> dict:
    """A close snapshot whose accounts_map lacks the entries named in ``gone``.

    The entries kept are the snapshot's own dicts, so what is computed onto them still lands
    where the page and the poll read it; the snapshot itself is never changed.
    """
    if not gone:
        return snapshot
    accounts_map = {
        account: [
            entry
            for entry in entries or []
            if not (isinstance(entry, dict) and entry.get("id") in gone)
        ]
        for account, entries in (snapshot.get("accounts_map") or {}).items()
    }
    return {**snapshot, "accounts_map": accounts_map}


def _held_state_kwargs(now_et: datetime | None = None) -> dict:
    """``{"state": <the saved bot_state without its no-longer-held entries>}`` for a route that
    reads a portfolio series without a request connection, so the value weights leave those
    entries out; ``{}`` when every saved symphony is held (the reader then loads the state
    itself, as it always has)."""
    try:
        state = database.load_state() or {}
    except Exception:
        return {}
    gone = _no_longer_held_ids(state, None, now_et)
    return {"state": _without_symphonies(state, gone)} if gone else {}


def _portfolio_hist_series(
    conn: sqlite3.Connection | None, state: dict | None
) -> tuple[list[str], list[float], list[float], float | None]:
    """(hist_dates, hist_bot, hist_held, vol_bot) for the hero chart and the vol row.

    vol_bot comes from the COMBINED portfolio return series (captures inter-symphony
    correlation); it stays None, never 0.0, when no shadow history exists. vol_held is
    not derived here: no portfolio-level held series exists to take it from, and copying
    vol_bot would fabricate a false tie. The (bot, held) series is the real pair, so the
    dashed "If held" line is genuine on first paint.
    """
    vol_bot: float | None = None
    hist_dates: list[str] = []
    hist_bot: list[float] = []
    hist_held: list[float] = []
    shadow = analytics.get_portfolio_daily_returns_from_shadow(conn=conn, state=state)
    if shadow is not None:
        shadow_dates, daily_returns = shadow
        vol_bot = analytics.compute_portfolio_annualized_vol(daily_returns)
        hist_dates = list(shadow_dates)
    try:
        pair = analytics.get_portfolio_bot_and_held_daily_returns(conn=conn, state=state)
        if pair is not None:
            pair_dates, pair_bot, pair_held = pair
            if not hist_dates:
                hist_dates = list(pair_dates)
            level_bot = 1.0
            level_held = 1.0
            for bot_pct, held_pct in zip(pair_bot, pair_held):
                level_bot *= 1.0 + bot_pct / 100.0
                level_held *= 1.0 + held_pct / 100.0
                hist_bot.append(round((level_bot - 1.0) * 100.0, 4))
                hist_held.append(round((level_held - 1.0) * 100.0, 4))
    except Exception:
        _daemon_log.error("_compute_portfolio_strip bot/held series failed", exc_info=True)
        _note_build_failure()
    return hist_dates, hist_bot, hist_held, vol_bot


_SNAPSHOT_CLOCK_FORMAT = "%H:%M:%S"  # captured_at_et is "<this> ET"


def _snapshot_clock_text(snapshot: dict) -> str:
    return str(snapshot.get("captured_at_et") or "").replace(" ET", "").strip()


def _snapshot_capture_moment(snapshot: dict) -> datetime | None:
    """The aware ET instant a close snapshot was captured, or None for a legacy snapshot
    that stored no trading day (its capture time of day alone cannot be dated)."""
    trading_day = snapshot.get("trading_day")
    if not trading_day:
        return None
    try:
        naive = _DatetimeType.strptime(
            f"{trading_day} {_snapshot_clock_text(snapshot)}", f"%Y-%m-%d {_SNAPSHOT_CLOCK_FORMAT}"
        )
    except ValueError:
        return None
    return naive.replace(tzinfo=_ET)


def _snapshot_is_previous_session(snapshot: dict, now_et: datetime | None = None) -> bool:
    """True when a newer session has finished than the one this snapshot closes.

    now_et is the caller's one request instant; the clock is read only without it.
    """
    trading_day = snapshot.get("trading_day")
    latest = market_calendar.most_recent_completed_session(
        now_et if now_et is not None else datetime.now(_ET)
    )
    return bool(trading_day) and latest is not None and trading_day < latest.isoformat()


def _snapshot_stamps(snapshot: dict, now_et: datetime | None = None) -> dict:
    """The date fields a frozen response states for its snapshot: trading_day, the
    previous-session marker, frozen_at and the one data_as_of stamp (now_et: the
    request instant, see _snapshot_is_previous_session)."""
    capture = _snapshot_capture_moment(snapshot)
    if capture is None:
        try:
            time_of_day = _DatetimeType.strptime(
                _snapshot_clock_text(snapshot), _SNAPSHOT_CLOCK_FORMAT
            ).time()
        except ValueError:
            time_of_day = None
        return {
            "trading_day": None,
            "snapshot_is_previous_session": False,
            "frozen_at": snapshot.get("captured_at_et"),
            "data_as_of": _format_data_stamp(time_of_day),
        }
    previous = _snapshot_is_previous_session(snapshot, now_et)
    return {
        "trading_day": snapshot["trading_day"],
        "snapshot_is_previous_session": previous,
        "frozen_at": _format_data_stamp(capture, seconds=True),
        "data_as_of": _format_data_stamp(capture, previous_session=previous),
    }


# ---------------------------------------------------------------------------
# The session a response describes
# ---------------------------------------------------------------------------
# One response describes one session. While the market is open that is today's session. From the
# close on it is the session that closed: its figures are served from its close snapshot, and every
# window counts back from that session's day. Nothing a closed market shows therefore moves with the
# wall clock (the host's clock is UTC, whose date turns over at 20:00 EDT, in the middle of a
# closed evening), and it changes only when the next session starts.

# Grace after the close within which the engine's close step stores the session's snapshot: the
# post-mortem cutoff (market_calendar.POST_MORTEM_GRACE_MINUTES), plus a minute. A snapshot still
# missing after it is reported (close_snapshot_missing).
_CLOSE_SNAPSHOT_GRACE = timedelta(minutes=market_calendar.POST_MORTEM_GRACE_MINUTES + 1)


@dataclass(frozen=True)
class _SessionView:
    """Which session a response describes, and what it is read from.

    kind: "open" (the live state), "frozen" (a stored close snapshot), "held" (the session has
    closed and its snapshot is not stored yet: the snapshot is built from the last committed state
    of that session, cut at the close, so the page never falls back to the session before) or
    "live" (the market is closed and no snapshot is stored: the live state).
    anchor: the ET trading day the figures belong to: the session a snapshot closes, otherwise
    today's ET date.
    snapshot: the close snapshot served (stored or built); None for "open" and "live".
    cut_state: the state whose cycle stamp cuts the shadow rows the response reads.
    latest: the newest completed session at the request instant.
    stalled_day: set only while the market is open and no cycle has committed since today's open:
    the ET day of the last committed cycle, the session whose figures the page still shows.
    """

    market_state: str
    kind: str
    anchor: date
    snapshot: dict | None
    cut_state: dict | None
    latest: date | None
    stalled_day: date | None = None

    @property
    def wording_day(self) -> date | None:
        """The session the page's words name ("Oct 30" in place of today or live): the closed
        market's anchor, or an open market's stalled day. None when the figures are today's."""
        if self.stalled_day is not None:
            return self.stalled_day
        return None if self.market_state == "open" else self.anchor

    @property
    def cutoff_anchor(self) -> date | None:
        """The day every window of a closed market counts back from: the anchor of the session its
        snapshot closes, so a window does not slide with the wall clock while nothing is trading.
        None while a session is open (a window counts back from today, which is the anchor) or no
        snapshot defines one: the windows then take their default, today's date."""
        return self.anchor if self.snapshot is not None else None

    @property
    def anchor_kwargs(self) -> dict:
        """{"anchor": day} for the analytics windows when cutoff_anchor names one, else {}."""
        anchor = self.cutoff_anchor
        return {} if anchor is None else {"anchor": anchor}


def _session_close_moment(day: date) -> datetime:
    """The aware ET instant the session of *day* closes (13:00 on an early-close day)."""
    return _DatetimeType.combine(day, market_calendar.session_close(day), tzinfo=_ET)


def _held_close_snapshot(state: dict, latest: date) -> dict | None:
    """The close snapshot of session *latest*, built from the live state, or None.

    Built only when the live state is of that session: the engine committed a cycle on or after
    its day and the state holds symphonies. It is shaped like the engine's own snapshot (trading
    day, capture time, symphony entries by account) and its capture moment is the last committed
    cycle, never later than the close, so it cuts the shadow rows exactly where a stored snapshot
    would.
    """
    stamp = _cycle_stamp_moment(state.get("last_successful_cycle_at"))
    if stamp is None or stamp.date() < latest:
        return None
    entries = {key: value for key, value in state.items() if _is_symphony_state_entry(value)}
    if not entries:
        return None
    captured = min(stamp, _session_close_moment(latest))
    accounts_map: dict[str, list[dict]] = {}
    for sym_id, entry in entries.items():
        accounts_map.setdefault(entry.get("account", "Unknown"), []).append(
            {"id": sym_id, **copy.deepcopy(entry)}
        )
    return {
        "built": True,
        "trading_day": latest.isoformat(),
        "captured_at_et": captured.strftime("%H:%M:%S ET"),
        "portfolio_strip": None,
        "shadow_divergence": {"by_symphony": {}, "portfolio": None},
        "accounts_map": accounts_map,
    }


def _stalled_day(state: dict, now_et: datetime) -> date | None:
    """The ET day of the last committed cycle when it is earlier than today's open, else None.

    No stamp at all is not stalled: there is no session to name. Age plays no part, only whether
    the stamp is before the open.
    """
    stamp = _cycle_stamp_moment(state.get("last_successful_cycle_at"))
    if stamp is None:
        return None
    today_open = _DatetimeType.combine(
        now_et.date(), market_calendar.session_open(now_et.date()), tzinfo=_ET
    )
    return stamp.date() if stamp < today_open else None


def _session_view(state: dict | None, market_state: str, now_et: datetime) -> _SessionView:
    """The session a response at *now_et* describes: the one place the window anchor is decided.

    Open: today's session, read from the live state. Closed or pre-market with a stored snapshot:
    the snapshot's own session, unless a newer session has closed without its snapshot being
    stored yet and the live state is of that session, in which case that session's last committed
    state is served ("held"), cut at its close. Closed with no snapshot at all: the live state.
    """
    state = state or {}
    try:
        latest = market_calendar.most_recent_completed_session(now_et)
    except Exception:
        latest = None
    stalled = _stalled_day(state, now_et) if market_state == "open" else None
    snapshot = state.get("last_market_close_snapshot")
    if market_state == "open" and (stalled is None or not snapshot):
        return _SessionView(market_state, "open", now_et.date(), None, state, latest, stalled)
    # A stalled open (no cycle since today's open) serves the view pre-market serves for the same
    # stored state: the last session's close snapshot, until the first cycle commits.
    if not snapshot:
        return _SessionView(market_state, "live", now_et.date(), None, state, latest)
    kind = "frozen"
    stored_day = snapshot.get("trading_day")
    if stored_day and latest is not None and str(stored_day) < latest.isoformat():
        held = _held_close_snapshot(state, latest)
        if held is not None:
            snapshot, kind = held, "held"
    try:
        anchor = date.fromisoformat(str(snapshot.get("trading_day")))
    except ValueError:
        anchor = latest or now_et.date()
    capture = _snapshot_capture_moment(snapshot)
    cut_state = state if capture is None else {"last_successful_cycle_at": capture.isoformat()}
    return _SessionView(market_state, kind, anchor, snapshot, cut_state, latest, stalled)


def _window_cutoff_of(trading_session: _SessionView, window: object) -> date | None:
    """The cutoff of *window* for a route that reads the session's figures: counted back from the
    closed session's anchor, else from today (analytics.window_cutoff / _window_cutoff_date)."""
    anchor = trading_session.cutoff_anchor
    if anchor is None:
        return analytics._window_cutoff_date(window)
    return analytics.window_cutoff(window, anchor)


def _current_session(now_et: datetime | None = None) -> tuple[_SessionView, dict]:
    """(session, live state) at one request instant: for a route that reads no portfolio view of
    its own but still states a window or cuts rows at the close. A state that cannot be read
    reads as empty, so the route keeps answering from what it has."""
    if now_et is None:
        now_et = datetime.now(_ET)
    try:
        state = database.load_state() or {}
    except Exception:
        state = {}
    return _session_view(state, get_market_state(now_et), now_et), state


# ---------------------------------------------------------------------------
# The results of a closed market, held
# ---------------------------------------------------------------------------
# The figures of a closed market cannot change, so a poll of one is a lookup, not a recomputation
# (a recomputation reads a table of hundreds of thousands of rows dozens of times, and a poll is
# abandoned at 20 s). A result is held per snapshot identity (and per window); a new snapshot, or
# anything else the identity names, is a different key, so the old result is simply no longer asked
# for. Held results are bounded in snapshots, safe to build from several request threads at once
# (one build per key, the rest wait for it), and never handed out: a caller gets its own copy.

_HOLD_MAX_IDENTITIES = 4  # snapshot identities kept: today's, plus the few before it
_HOLD_MAX_RESULTS = 40  # results kept per identity: the poll, and two per window token
_NOTHING = object()

# Set around a build the hold runs: a build that catches its own fault and answers without the
# failed part records it here, and the hold then answers it without storing it. A part that is
# merely absent (a closed session with no history) is a complete build and is held.
# The variable and recorder live in analytics (which cannot import app) so its own swallowed reads
# can record too.
_BUILD_FAILURES = analytics.BUILD_FAILURES
_note_build_failure = analytics.note_build_failure


class _ClosedHold:
    def __init__(self, max_identities: int, max_results: int) -> None:
        self._max_identities = max_identities
        self._max_results = max_results
        self._lock = threading.Lock()  # guards the two dicts below, never held across a build
        self._held: _collections.OrderedDict[tuple, dict[object, object]] = (
            _collections.OrderedDict()
        )
        self._building: dict[tuple, threading.Lock] = {}

    def _get(self, identity: tuple, name: object) -> object:
        with self._lock:
            results = self._held.get(identity)
            if results is None or name not in results:
                return _NOTHING
            self._held.move_to_end(identity)
            return results[name]

    def _put(self, identity: tuple, name: object, value: object) -> None:
        with self._lock:
            results = self._held.setdefault(identity, {})
            results[name] = value
            while len(results) > self._max_results:
                results.pop(next(iter(results)))
            self._held.move_to_end(identity)
            while len(self._held) > self._max_identities:
                self._held.popitem(last=False)

    def get_or_build(self, identity: tuple, name: object, build: Callable[[], object]) -> object:
        """The held result, or the result of build() (held for the next caller). A copy either way."""
        found = self._get(identity, name)
        if found is not _NOTHING:
            return copy.deepcopy(found)
        key = (identity, name)
        with self._lock:
            build_lock = self._building.setdefault(key, threading.Lock())
        try:
            with build_lock:
                found = self._get(identity, name)
                if found is not _NOTHING:
                    return copy.deepcopy(found)
                failures: list[bool] = []
                token = _BUILD_FAILURES.set(failures)
                try:
                    value = build()
                finally:
                    _BUILD_FAILURES.reset(token)
                if not failures:
                    self._put(identity, name, copy.deepcopy(value))
                return value
        finally:
            with self._lock:
                self._building.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._held.clear()


_CLOSED_HOLD = _ClosedHold(_HOLD_MAX_IDENTITIES, _HOLD_MAX_RESULTS)

# The connection the warm-up reads every one of its results through (see _warm_closed_results): set
# only on the warm-up's own thread, so a request builds with a connection of its own.
_WARMUP_CONN: contextvars.ContextVar[sqlite3.Connection | None] = contextvars.ContextVar(
    "closed_warmup_conn", default=None
)


def _closed_conn(trading_session: _SessionView) -> tuple[sqlite3.Connection, bool]:
    """(connection cut at the session's snapshot, whether the caller must close it).

    The warm-up shares ONE connection across everything it builds, so a read one result makes
    (a symphony's trajectory, the portfolio series) is not made again for the next; any other
    caller opens its own.
    """
    shared = _WARMUP_CONN.get()
    if shared is not None:
        return shared, False
    return _committed_shadow_conn(trading_session.cut_state), True


# Window tokens a result is held for (the picker's); any other token is computed each time.
_HELD_WINDOW_TOKENS = frozenset({"30d", "60d", "90d", "125d", "ytd", "1y", "all"})


def _closed_identity(trading_session: _SessionView, now_et: datetime) -> tuple:
    """The key a closed market's results are held under.

    A stored snapshot is named by its day and capture time, and by the newest completed session
    (a newer close changes which symphonies are no longer held). The built snapshot of a session
    that is still waiting for its own is named by that session alone, so it holds until the real
    one is stored. The account totals are not part of it: they are added to the held result on
    each request (_compute_frozen_portfolio_strip), so a refresh never makes it stale.
    """
    snapshot = trading_session.snapshot
    anchor = trading_session.anchor.isoformat()
    if trading_session.kind == "held":
        return ("held", anchor)
    return (
        "frozen",
        snapshot.get("trading_day"),
        snapshot.get("captured_at_et"),
        anchor,
        trading_session.latest.isoformat() if trading_session.latest is not None else None,
    )


def _held_closed(
    trading_session: _SessionView, now_et: datetime, name: object, build: Callable[[], object]
) -> object:
    """build()'s result, held for a closed market's session (an open one computes every time)."""
    if trading_session.snapshot is None:
        return build()
    return _CLOSED_HOLD.get_or_build(_closed_identity(trading_session, now_et), name, build)


def _closed_poll_view(
    trading_session: _SessionView, state: dict | None, now_et: datetime
) -> tuple[dict, dict]:
    """(portfolio view, hero/meta strip) of a closed market; the caller owns its copy.

    What /api/state and the page share. The part that reads the shadow rows is held per snapshot
    and computed once; the account totals are added to it on each request.
    """

    def build() -> dict:
        conn, owned = _closed_conn(trading_session)
        try:
            parts = _frozen_view_parts(trading_session, conn, now_et)
            rows = parts["rows"]
            # The hero arrays come from a second source when the snapshot's own series is empty.
            hist = rows.get("hist")
            parts["hist_fallback"] = (
                None
                if rows.get("ok") and hist and hist[0]
                else _hist_fallback(conn, parts["weights_state"])
            )
        finally:
            if owned:
                conn.close()
        return parts

    parts = _held_closed(trading_session, now_et, "poll", build)
    view = _frozen_view(parts, now_et)
    strip = view["portfolio_strip"]
    fallback = parts["hist_fallback"]
    return view, ({**fallback, **strip} if fallback is not None else strip)


def _closed_hero_body(
    trading_session: _SessionView, window: str, now_et: datetime
) -> dict[str, object]:
    """The hero chart of a closed market's window, read from the snapshot's rows and weights."""

    def build() -> dict:
        conn, owned = _closed_conn(trading_session)
        try:
            weights = _frozen_symphony_inputs(trading_session.snapshot)[1]
            weights = _without_symphonies(weights, _no_longer_held_ids(weights, conn, now_et))
            return _hero_chart_body(window, conn, weights, trading_session.anchor)
        finally:
            if owned:
                conn.close()

    if window not in _HELD_WINDOW_TOKENS:
        return build()
    return _held_closed(trading_session, now_et, ("hero", window), build)


def _frozen_symphony_inputs(snapshot: dict) -> tuple[list[dict], dict]:
    """Analytics inputs (symphonies list, bot_state-shaped map) from a close snapshot."""
    trading_day = snapshot.get("trading_day")
    symphonies: list[dict] = []
    bot_state: dict = {}
    for entries in (snapshot.get("accounts_map") or {}).values():
        for sym in entries or []:
            if not isinstance(sym, dict):
                continue
            sym_id = sym.get("id", "")
            current_return = sym.get("current_return") or 0.0
            value = sym.get("current_value") or 0.0
            symphonies.append(
                {
                    "id": sym_id,
                    "value": value,
                    "last_percent_change": current_return / 100.0,
                    "simple_return": sym.get("simple_return"),
                    "net_deposits": sym.get("net_deposits"),
                    "time_weighted_return": sym.get("time_weighted_return"),
                    "max_drawdown": sym.get("max_drawdown"),
                    "trading_day": trading_day,
                }
            )
            bot_state[sym_id] = {
                "current_value": value,
                "current_return": current_return,
                "name": sym.get("name"),
                "account": sym.get("account"),
                # DE-HELD-BASIS-001 F5(b): hand-built, not the real bot_state entry, so
                # the reconstruction marker analytics reads from it must be added here.
                "current_return_is_reconstructed": sym.get(
                    "current_return_is_reconstructed", False
                ),
            }
    return symphonies, bot_state


# Fields a snapshot entry may lack; _normalize_frozen_symphonies reads these defaults and
# fills each missing field, so a consumer sees a float or None, never an absent key.
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


def _normalize_frozen_symphonies(snapshot: dict) -> None:
    for entries in (snapshot.get("accounts_map") or {}).values():
        for sym in entries or []:
            if isinstance(sym, dict):
                for field, default in _FROZEN_SYM_DEFAULTS.items():
                    sym.setdefault(field, default)


def _apply_default_window_legs(strip: dict, windowed: dict) -> None:
    """Put the default window's Max DD legs and volatility on a portfolio strip, under their own keys.

    The hero's Max DD and Ann. Vol rows follow the picked window, and the dashboard opens on the
    default one, so the first paint prints that window's figures: the first paint, the window's own
    strip (``/api/strip/<window>``) and the row after a refresh then say one thing. They are kept
    apart from the strip's lifetime figures (``max_drawdown``, ``vol_bot``/``vol_held``), which
    stay exactly as they were: the cards and the lifetime line read those. ``n_obs`` is the days
    the window held, which decides whether the row has a figure to show. A window that supplies
    no figure leaves its legs None (the row then says so), never the lifetime figure under the
    window's name.
    """
    legs = windowed.get("max_drawdown")
    if isinstance(legs, dict):
        day_count = windowed.get("window_day_count")
        strip["window_max_drawdown"] = {
            "dry_run": legs.get("dry_run"),
            "if_held": legs.get("if_held"),
            "n_obs": day_count if isinstance(day_count, int) else 0,
        }
    if "vol_bot" in windowed or "vol_held" in windowed:
        strip["window_vol_bot"] = windowed.get("vol_bot")
        strip["window_vol_held"] = windowed.get("vol_held")


def _day_anchor(trading_day: object) -> date | None:
    """The window anchor a response's ``trading_day`` text names, or None when it names none."""
    try:
        return date.fromisoformat(str(trading_day))
    except ValueError:
        return None


def _frozen_strip_rows(snapshot: dict, conn: sqlite3.Connection | None) -> dict:
    """The part of a close snapshot's strip that rests on shadow rows, not on account totals.

    Also attaches the per-symphony _tc/_cr/_mdd to the snapshot entries. Everything is read for
    snapshot["trading_day"], never today's date, from the snapshot and the rows `conn` exposes
    (cut at the capture moment); the live bot_state and later rows cannot move a figure. This is
    the expensive half of the strip (dozens of reads of a large table), so a closed market holds
    it per snapshot; _compute_frozen_portfolio_strip adds the account totals to it on each request.

    Returns {"ok": bool, "vw_tc", "vw_cr", "max_drawdown", "default" (the default window's
    strip or None), "hist" (the hero series or None)}; "ok" is False when the portfolio figures
    could not be computed.
    """
    trading_day = snapshot.get("trading_day")
    for entries in (snapshot.get("accounts_map") or {}).values():
        for sym in entries or []:
            if not isinstance(sym, dict):
                continue
            sym_dict = {
                "id": sym.get("id", ""),
                "value": sym.get("current_value") or 0.0,
                "last_percent_change": (sym.get("current_return") or 0.0) / 100.0,
                "simple_return": sym.get("simple_return"),
                "net_deposits": sym.get("net_deposits"),
                "time_weighted_return": sym.get("time_weighted_return"),
                "max_drawdown": sym.get("max_drawdown"),
                "trading_day": trading_day,
            }
            for key, reader in (
                ("_tc", analytics.get_symphony_today_change),
                ("_cr", analytics.get_symphony_cumulative_return),
                ("_mdd", analytics.get_symphony_max_drawdown),
            ):
                try:
                    sym[key] = reader(sym_dict, sym, trading_day=trading_day, conn=conn)
                except (KeyError, TypeError, ValueError):
                    sym[key] = {"if_held": None, "dry_run": None}
                    _note_build_failure()

    symphonies_list, snap_bot_state = _frozen_symphony_inputs(snapshot)
    rows: dict = {"ok": False, "default": None, "hist": None}
    try:
        # include_paired_guard_delta=True: the Tier-2 floor re-derives dry_run from the
        # coverage-scaled guard_delta_vw (DE-HELD-BASIS-001 F5(a)/F6).
        rows["vw_tc"] = analytics.get_portfolio_today_change(
            symphonies_list,
            snap_bot_state,
            trading_day=trading_day,
            conn=conn,
            include_paired_guard_delta=True,
        )
        rows["vw_cr"] = analytics.get_portfolio_cumulative_return(
            symphonies_list, snap_bot_state, trading_day=trading_day, conn=conn
        )
        rows["max_drawdown"] = analytics.get_portfolio_max_drawdown(
            symphonies_list,
            snap_bot_state,
            trading_day=trading_day,
            conn=conn,
            state=snap_bot_state,
        )
    except Exception:
        _note_build_failure()
        return rows
    rows["ok"] = True

    # Its own try: a failure here must not discard the figures computed above. The
    # windowed cumulative return is left out (no client reads it from this strip).
    try:
        default = analytics.compute_windowed_portfolio_strip(
            symphonies_list,
            snap_bot_state,
            window=_DEFAULT_HERO_WINDOW,
            conn=conn,
            state=snap_bot_state,
            anchor=_day_anchor(trading_day),
        )
        if isinstance(default, dict):
            rows["default"] = default
    except Exception:
        _note_build_failure()
        _daemon_log.error("closed_frozen default-window strip failed", exc_info=True)

    try:
        rows["hist"] = _portfolio_hist_series(conn, snap_bot_state)
    except Exception:
        _note_build_failure()
        _daemon_log.error("closed_frozen hero series failed", exc_info=True)
    return rows


def _compute_frozen_portfolio_strip(
    snapshot: dict, rows: dict, now_et: datetime | None = None
) -> dict:
    """portfolio_strip for the market-close snapshot (authoritative).

    `rows` is _frozen_strip_rows of the snapshot (held per snapshot). The strip is that, plus the
    snapshot and the account totals latched for it: the live bot_state, later rows and later
    account refreshes cannot move a figure.
    """
    symphonies_list, snap_bot_state = _frozen_symphony_inputs(snapshot)
    data_as_of = _snapshot_stamps(snapshot, now_et)["data_as_of"]

    # ONE source for every account figure: the totals latched for this snapshot, else the
    # last good read (labelled stale). Never the live cache, which a later refresh moves.
    held = _close_totals_for(snapshot)
    if held is not None:
        totals, from_last_good = held["totals"], False
    else:
        totals, from_last_good = _account_totals_last_good, True

    degraded = {
        "today_change": None,
        "cumulative_return": None,
        "max_drawdown": None,
        "account_value": totals.get("portfolio_value"),
        "data_as_of": data_as_of,
    }
    if not rows.get("ok"):
        return degraded

    try:
        symphony_value_sum = sum(s.get("value", 0.0) or 0.0 for s in symphonies_list)

        # TC, CR and value resolve INDEPENDENTLY, so a missing field never degrades a
        # warm sibling.
        account_tc = totals.get("portfolio_tc")
        tc_stale = from_last_good and account_tc is not None
        account_cr = totals.get("portfolio_cr")
        cr_stale = from_last_good and account_cr is not None
        cached_value = totals.get("portfolio_value")

        basis_stale = tc_stale or cr_stale
        basis_as_of = (_account_totals_last_success_at or _NO_STAMP) if basis_stale else None

        vw_tc = rows["vw_tc"]
        vw_cr = rows["vw_cr"]

        if account_tc is not None:
            today_change = analytics.get_portfolio_today_change_account_basis(
                vw_tc, account_tc, cached_value or 0.0, symphony_value_sum
            )
        else:
            today_change = _project_today_change_floor(vw_tc)

        if account_cr is not None:
            cumulative_return = analytics.get_portfolio_cumulative_return_account_basis(
                vw_cr, account_cr, cached_value or 0.0, symphony_value_sum
            )
        else:
            cumulative_return = vw_cr

        strip: dict = {
            "today_change": today_change,
            "cumulative_return": cumulative_return,
            "max_drawdown": rows["max_drawdown"],
            "account_value": (
                cached_value
                if cached_value is not None
                else sum(
                    v.get("current_value") or 0.0
                    for v in snap_bot_state.values()
                    if isinstance(v, dict)
                )
            ),
            "data_as_of": data_as_of,
        }
        # Tier 2 honest floor: either field has no basis at all, so a value on the strip
        # is raw value-weighted.
        if account_tc is None or account_cr is None:
            strip["basis"] = "value_weighted"
        if basis_stale:
            strip["account_basis_stale"] = True
            strip["account_basis_as_of"] = basis_as_of
        # The Composer account-lifetime CR, the "Account · all-time" stat.
        if isinstance(account_cr, (int, float)):
            strip["account_all_time_cr"] = account_cr

        default = rows.get("default")
        if isinstance(default, dict):
            guard_alpha = default.get("guard_alpha")
            strip["guard_alpha"] = guard_alpha if isinstance(guard_alpha, (int, float)) else None
            window = default.get("window", _DEFAULT_HERO_WINDOW)
            strip["window"] = window if isinstance(window, str) else _DEFAULT_HERO_WINDOW
            _apply_default_window_legs(strip, default)

        hist = rows.get("hist")
        if hist is not None:
            hist_dates, hist_bot, hist_held, vol_bot = hist
            strip.update(
                {
                    "vol_bot": vol_bot,
                    "vol_held": None,
                    "hist_dates": hist_dates,
                    "hist_bot": hist_bot,
                    "hist_held": hist_held,
                    "hist_source": "shadow_history",
                }
            )
            if isinstance(default, dict):
                # The update above wrote the lifetime vol; the window's figures stand.
                _apply_default_window_legs(strip, default)
        return strip
    except Exception:
        return degraded


def _frozen_view_parts(
    trading_session: _SessionView, conn: sqlite3.Connection | None, now_et: datetime
) -> dict:
    """What a close snapshot's view rests on that needs the shadow rows (held per snapshot): the
    state served to the cards, the snapshot (symphonies no longer held left out, per-symphony legs
    attached), the strip's rows, the value weights and the symphonies left out. The entries of
    "state" and of the snapshot are the same objects, as the page and the poll both rely on."""
    snapshot = trading_session.snapshot
    served: dict = {}
    for entries in (snapshot.get("accounts_map") or {}).values():
        for sym in entries or []:
            if isinstance(sym, dict) and "id" in sym:
                served[sym["id"]] = sym
    # A symphony no longer held is left out of the snapshot's cards and portfolio figures,
    # asked through the connection cut at the capture so the answer is fixed with them.
    gone = _no_longer_held_ids(served, conn, now_et)
    served = _without_symphonies(served, gone)
    snapshot = _snapshot_without_symphonies(snapshot, gone)
    rows = _frozen_strip_rows(snapshot, conn)
    return {
        "state": served,
        "snapshot": snapshot,
        "rows": rows,
        "weights_state": _frozen_symphony_inputs(snapshot)[1],
        "no_longer_held": gone,
    }


def _frozen_view(parts: dict, now_et: datetime) -> dict:
    """The "frozen" view of a close snapshot: its parts plus the strip, with the account totals
    latched for the snapshot added (a cheap step, taken on each request)."""
    return {
        "branch": "frozen",
        "state": parts["state"],
        "snapshot": parts["snapshot"],
        "portfolio_strip": _compute_frozen_portfolio_strip(
            parts["snapshot"], parts["rows"], now_et
        ),
        "weights_state": parts["weights_state"],
        "no_longer_held": parts["no_longer_held"],
    }


def _portfolio_view(
    state: dict,
    trading_session: _SessionView,
    conn: sqlite3.Connection | None,
    now_et: datetime | None = None,
) -> dict:
    """The ONE portfolio computation of a request, shared by the poll and the page.

    branch "frozen": after the close, from the session's close snapshot (stored, or built from
    the last committed state while the session's own is not stored yet); the served state and
    the strip both come from the snapshot. "waiting": no state yet. "live": everything else.
    Returns {"branch", "state" (the state served to cards), "snapshot", "portfolio_strip",
    "weights_state", "no_longer_held"}. The served state, the snapshot and every figure of the
    strip leave out the symphonies the shared rule finds no longer held ("no_longer_held" names
    them); the frozen branch asks it through the connection cut at the capture.
    now_et is the request's one clock reading; without it the clock is read here.
    """
    if now_et is None:
        now_et = datetime.now(_ET)
    if trading_session.snapshot is not None:
        return _frozen_view(_frozen_view_parts(trading_session, conn, now_et), now_et)
    branch = "live" if state else "waiting"
    trading_day = trading_session.anchor.isoformat()
    gone = _no_longer_held_ids(state, conn, now_et) if state else frozenset()
    state = _without_symphonies(state, gone) if state else state
    strip = _compute_portfolio_strip(state or {}, trading_day=trading_day, conn=conn)
    return {
        "branch": branch,
        "state": state,
        "snapshot": None,
        "portfolio_strip": strip,
        "weights_state": state,
        "no_longer_held": gone,
    }


# Shown wherever a moment is unknown; never replaced by the request time.
_NO_STAMP = "--"
_PREVIOUS_SESSION_SUFFIX = " · previous session"


def _format_data_stamp(
    moment: object, *, seconds: bool = False, previous_session: bool = False
) -> str:
    """The one writer of a data-freshness stamp.

    An aware datetime reads "YYYY-MM-DD HH:MM ET" in Eastern time (seconds when asked,
    truncated never rounded); a bare time of day reads "HH:MM ET (date not recorded)".
    Anything else, including a naive datetime whose zone would be a guess, reads "--".
    Never reads the clock and never raises.
    """
    if isinstance(moment, dt_time):
        return f"{moment.hour:02d}:{moment.minute:02d} ET (date not recorded)"
    if not isinstance(moment, _DatetimeType) or moment.tzinfo is None:
        return _NO_STAMP
    try:
        eastern = moment.astimezone(_ET)
        text = eastern.strftime("%Y-%m-%d %H:%M:%S ET" if seconds else "%Y-%m-%d %H:%M ET")
    except (OverflowError, ValueError, OSError):
        return _NO_STAMP
    return text + _PREVIOUS_SESSION_SUFFIX if previous_session else text


def _et_instant_of(day_text: object, clock_text: object) -> _DatetimeType | None:
    """An Eastern-aware instant from a stored date and a stored time of day, or None.

    ``clock_text`` is a bare time ("HH:MM" or "HH:MM:SS", read as Eastern, as the post-mortem
    producer writes ``time_triggered``) or a full ISO instant (a naive one is Eastern too). A missing
    or unreadable time is None: a date alone is not an instant, so it never becomes a stamp.
    """
    if not isinstance(clock_text, str) or not clock_text.strip():
        return None
    text = clock_text.strip()
    candidates = [f"{day_text}T{text}", text] if isinstance(day_text, str) else [text]
    for candidate in candidates:
        try:
            moment = _DatetimeType.fromisoformat(candidate)
        except ValueError:
            continue
        return moment.replace(tzinfo=_ET) if moment.tzinfo is None else moment
    return None


def _newest_observation_stamp(conn: sqlite3.Connection | None) -> str:
    """Dated stamp of the newest shadow_history row `conn` exposes; "--" when there is none."""
    own = None
    moment = None
    try:
        if conn is None:
            own = conn = sqlite3.connect(
                f"file:{analytics._get_shadow_db_file()}?mode=ro", uri=True, timeout=10.0
            )
        row = conn.execute(
            "SELECT ts_utc FROM shadow_history ORDER BY ts_utc DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            moment = _DatetimeType.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
    except Exception:
        moment = None
    finally:
        if own is not None:
            own.close()
    return _format_data_stamp(moment)


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

        # AC-1 (DE-PERF-WINDOW-TRUTH-001, supersedes the old "D-02" warm-cache
        # override): if_held/dry_run now come UNCONDITIONALLY from
        # analytics.get_portfolio_max_drawdown, regardless of cache warmth --
        # both legs are a genuine same-window, normalized peak-to-trough pair
        # computed entirely from shadow_history (see that function's docstring).
        # The warm-cache Composer lifetime scalar
        # (_account_totals_cache["portfolio_mdd"]) must NEVER be a comparison
        # leg again -- that was the #1 defect this cycle fixes (a lifetime
        # scalar diffed against a few days of shadow history). It is REPURPOSED
        # (kept, not deleted) as the PREFERRED source for the separate AC-2
        # "Lifetime Max Drawdown" figure when warm, since it reflects Composer's
        # true account-lifetime peak-to-trough more precisely than the
        # value-weighted aggregate of per-symphony scalars
        # (analytics.get_portfolio_max_drawdown's own if_held_lifetime, the
        # cold-cache/fallback source for the same figure).
        #
        # D8 sign convention preserved: abs()-convert the warm-cache scalar at
        # this consumer boundary (Composer's raw field is conventionally
        # NEGATIVE, app.py:272); analytics.get_portfolio_max_drawdown's own
        # if_held_lifetime is already positive magnitude.
        max_drawdown: dict = analytics.get_portfolio_max_drawdown(
            symphonies_list, bot_state, trading_day=trading_day, conn=conn, state=bot_state
        )
        _cached_mdd = _account_totals_cache.get("portfolio_mdd")
        if _cached_mdd is not None:
            max_drawdown = dict(max_drawdown)
            max_drawdown["if_held_lifetime"] = abs(_cached_mdd)

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
        vol_held: float | None = None
        # AC-4c: the series carries its DATES so _build_meta can derive
        # insufficient_history; without them every portfolio read as short history.
        hist_dates, hist_bot, hist_held, vol_bot = _portfolio_hist_series(conn, bot_state)

        # The stamp names the newest observation of the completed cycle (the connection
        # sees only completed cycles), never the cycle stamp and never the request time.
        _data_as_of = _newest_observation_stamp(conn)

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
        if _live_basis_stale and _account_basis_past_grace():
            _strip["account_basis_stale"] = True
            _strip["account_basis_as_of"] = _account_totals_last_success_at or _NO_STAMP

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
                symphonies_list,
                bot_state,
                window=_DEFAULT_HERO_WINDOW,
                trading_day=trading_day,
                conn=conn,
                state=bot_state,
            )
            if isinstance(_default, dict):
                _ga = _default.get("guard_alpha")
                _strip["guard_alpha"] = _ga if isinstance(_ga, (int, float)) else None
                _win = _default.get("window", _DEFAULT_HERO_WINDOW)
                _strip["window"] = _win if isinstance(_win, str) else _DEFAULT_HERO_WINDOW
                _apply_default_window_legs(_strip, _default)
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
            "data_as_of": _NO_STAMP,
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

    return {
        "bot_state": bot_state,
        "is_locked": is_locked,
        "port_state": port_state,
        "exit_authority": exit_authority,
        "daemon_started_at": _DAEMON_STARTED_AT,
    }


@app.route("/api/events")
def sse_events():
    """Server-Sent Events endpoint — streams cycle-complete notifications (AC-2).

    Auth-gated by _auth_before_request like all /api/ routes.
    Each connected client gets a Queue; _notify_cycle_complete() fans out to all.
    Heartbeat comment every 15 s keeps the connection alive.
    """

    client_q: queue.Queue = queue.Queue()
    # The generator runs outside the request context, so read the session here.
    enforce_session = _auth_check_enabled
    issued = session.get("issued_at_ns")
    logged_in_at = session.get("logged_in_at_ns")
    generation = session.get(_SESSION_GENERATION_KEY)

    def session_ended() -> bool:
        return enforce_session and not _session_issue_is_valid(
            issued, logged_in_at, _session_now_ns(), generation
        )

    def generate():
        with _sse_clients_lock:
            _sse_clients.append(client_q)
        try:
            yield "retry: 5000\n\n"  # headers and reconnect delay reach the client at once
            while True:
                try:
                    name, data = client_q.get(timeout=_SSE_HEARTBEAT_S)
                    if session_ended():
                        return
                    yield f"event: {name}\ndata: {data}\n\n"
                except queue.Empty:
                    if session_ended():
                        return
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


def _et_trading_day_bounds(now_et: datetime) -> tuple[str, str]:
    """Return (ET date, UTC instant of that ET midnight) for one request instant.

    The trigger table stores UTC timestamps, so "today's" lower bound is ET midnight
    converted to UTC (04:00Z or 05:00Z by DST), not the ET date with a UTC midnight.
    """
    et_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start = et_midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return now_et.strftime("%Y-%m-%d"), utc_start


def _strip_with_hist_fallback(
    strip: dict, conn: sqlite3.Connection | None, state: dict | None
) -> dict:
    """The strip, with hist arrays from a continuous daily source when it has none.

    Preference: shadow_history (written every cycle for every tracked symphony), then the
    post_mortem files (exit-trigger days only, a biased partial curve). The strip's own
    keys win over the fallback's.
    """
    if strip and strip.get("hist_dates"):
        return strip
    fallback = _hist_fallback(conn, state)
    if fallback is not None:
        return {**fallback, **strip} if strip else fallback
    return strip


def _hist_fallback(conn: sqlite3.Connection | None, state: dict | None) -> dict | None:
    """The hero arrays of the continuous daily source with the most days, or None when neither
    shadow_history nor the post_mortem files has any (or the read fails). Never raises."""
    try:
        shadow_strip = None
        shadow = analytics.get_portfolio_daily_returns_from_shadow(conn=conn, state=state)
        if shadow is not None:
            shadow_dates, shadow_daily = shadow
            series: list[float] = []
            level = 1.0
            for daily in shadow_daily:
                level *= 1.0 + daily / 100.0
                series.append((level - 1.0) * 100.0)
            # Bot and held share one series: shadow_return tracks the live portfolio
            # continuously, and divergence appears once post-trigger tracking matures.
            shadow_strip = {
                "hist_dates": shadow_dates,
                "hist_bot": series,
                "hist_held": series,
                "hist_source": "shadow_history",
            }

        # weight_by="value" matches the real post_mortem shape; "weight" is the field
        # name chart_archive stubs use.
        pm_strip = None
        history = analytics.get_history_with_cache_invalidation(
            base_dir=analytics._POST_MORTEMS_DIR
        )
        if history:
            agg_dates, agg_held, agg_bot = analytics.compute_aggregate_returns(history)
            if not agg_dates:
                agg_dates, agg_held, agg_bot = analytics.compute_aggregate_returns(
                    history, weight_by="weight"
                )
            if agg_dates:
                hist_bot: list[float] = []
                hist_held: list[float] = []
                level_bot = 1.0
                level_held = 1.0
                for bot_pct, held_pct in zip(agg_bot, agg_held):
                    level_bot *= 1.0 + bot_pct / 100.0
                    level_held *= 1.0 + held_pct / 100.0
                    hist_bot.append((level_bot - 1.0) * 100.0)
                    hist_held.append((level_held - 1.0) * 100.0)
                pm_strip = {
                    "hist_dates": agg_dates,
                    "hist_bot": hist_bot,
                    "hist_held": hist_held,
                    "hist_source": "post_mortem",
                }

        # The source with more days; shadow wins a tie (continuous).
        shadow_days = len((shadow_strip or {}).get("hist_dates", []))
        pm_days = len((pm_strip or {}).get("hist_dates", []))
        if shadow_days >= pm_days and shadow_strip is not None:
            fallback = shadow_strip
        elif pm_strip is not None:
            fallback = pm_strip
        else:
            fallback = shadow_strip
        return fallback
    except Exception as hist_exc:
        _daemon_log.error(
            "get_state() hist-series build failed — hero chart will be empty: %s",
            hist_exc,
            exc_info=True,
        )
        _note_build_failure()
    return None


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


def _mdd_lifetime(s: dict):
    """Composer's lifetime max-drawdown scalar for a card, or None."""
    mdd = s.get("_mdd")
    return mdd.get("if_held_lifetime") if isinstance(mdd, dict) else None


def _symphony_card(sym_id: str, s: dict) -> dict:
    """One card of the /api/state `symphonies` list, built from a state or snapshot entry."""
    _tc_b, _tc_h, _cr_b, _cr_h, _mdd_b, _mdd_h = _tc_cr_mdd_floats(s)
    return {
        "id": sym_id,
        "account": s.get("account"),
        "status": _sym_status(s),
        # Raw bot_state fields needed for card status pill + MC dial refresh.
        "current_return": s.get("current_return"),
        "stop_trigger": s.get("stop_trigger"),
        "mc_prob": s.get("mc_prob"),
        "armed": bool(s.get("armed")),
        "tp_armed": bool(s.get("tp_armed")),
        "para_armed": bool(s.get("para_armed")),
        "triggered": bool(s.get("triggered")),
        "triggered_reason": s.get("triggered_reason"),
        # guard_alpha for the outcome banner on triggered cards (see _inject_guard_alpha).
        "guard_alpha": s.get("guard_alpha"),
        # Analytics-derived display values.
        "tc_bot": _tc_b,
        "tc_held": _tc_h,
        "cr_bot": _cr_b,
        "cr_held": _cr_h,
        "mdd_bot": _mdd_b,
        "mdd_held": _mdd_h,
        # The figure the card prints as "Lifetime MDD": Composer's own lifetime scalar, kept apart
        # from the bot/held comparison. None when there is none (the card then prints "--").
        "mdd_lifetime": _mdd_lifetime(s),
    }


@app.route("/api/state")
@_shares_reads
def get_state():
    _shadow_conn = None
    try:
        _ro_conn = database.get_ro_connection()
        # One ET reading per request so every date lookup agrees across midnight.
        _req_now_et = datetime.now(_ET)
        market_state = get_market_state(_req_now_et)

        # AC-P2.12.2: additive fields — computed once, merged into every response branch.
        _api_state = get_api_state_dict()
        _additive_env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
        _additive_live_mode = env_flags.is_true(_additive_env.get("LIVE_EXECUTION", "False"))
        _additive = {
            "port_state": _api_state.get("port_state", {}),
            "exit_authority": _api_state.get("exit_authority", {}),
            "daemon_started_at": _DAEMON_STARTED_AT,
            "live_mode": _additive_live_mode,
        }
        state_data = database.load_state()
        _additive["engine_phase"] = _engine_phase(
            _req_now_et,
            market_state,
            (state_data or {}).get("last_successful_cycle_at"),
        )
        # One health reading per response, so the header label and the body agree.
        _additive["degraded_reasons"] = _degraded_reasons(state_data, _req_now_et)
        # ONE committed-cycle connection and ONE portfolio computation per response: the
        # hero (meta), the rows (portfolio_strip) and the cards all read from them. A closed
        # market's computation is held per snapshot (_closed_poll_view): its polls read no rows.
        trading_session = _session_view(state_data, market_state, _req_now_et)
        if trading_session.snapshot is not None:
            view, _meta_strip = _closed_poll_view(trading_session, state_data, _req_now_et)
        else:
            _shadow_conn = _committed_shadow_conn(trading_session.cut_state)
            view = _portfolio_view(state_data, trading_session, _shadow_conn, now_et=_req_now_et)
            _meta_strip = _strip_with_hist_fallback(
                view["portfolio_strip"], _shadow_conn, view["weights_state"]
            )
        if view["branch"] == "live":
            # The cards, counts and panels below read the state without the symphonies the
            # view found no longer held, so every figure of this response names one roster.
            state_data = view["state"]
        _portfolio_strip = view["portfolio_strip"]
        # The Sleeves and CVaR panels and the Edit Vars badge, read for the live symphonies (the
        # diagnostics and strategy rows are keyed by them, whichever state the cards show) from the
        # same state the page render uses, so the render and this poll print one thing. A builder
        # that fails must not take the response down: every part then reads as "could not read"
        # and the page keeps its own.
        try:
            _additive["dashboard_panels"] = _dashboard_panels_payload(
                _without_symphonies(state_data or {}, view.get("no_longer_held", frozenset())),
                trading_session.anchor,
            )
        except Exception as panels_exc:
            _daemon_log.warning("dashboard panels read failed: %s", type(panels_exc).__name__)
            _additive["dashboard_panels"] = _panels_unreadable()

        # AC-DM.3.3: closed + snapshot → serve frozen snapshot.
        if view["branch"] == "frozen":
            snapshot = view["snapshot"]
            if snapshot:
                # R2: remap shadow_divergence "portfolio" -> "portfolio_today" to match
                # the live path's key name (from database.get_shadow_divergence()).
                sd = dict(snapshot.get("shadow_divergence") or {})
                if "portfolio" in sd and "portfolio_today" not in sd:
                    sd["portfolio_today"] = sd.pop("portfolio")
                _alert = _active_fleet_alert()
                _state: dict = view["state"]
                sd_by_sym = sd.get("by_symphony") or {}
                for sym_id, entry in sd_by_sym.items():
                    if isinstance(entry, dict) and "name" not in entry:
                        entry = dict(entry)
                        sd_by_sym[sym_id] = entry
                        entry["name"] = (_state.get(sym_id) or {}).get("name") or sym_id

                # Each account list of the snapshot is ordered by name in place: get_or_build
                # hands out deep copies, so these lists are this request's own and sorting
                # them touches no one else's.
                _snap_accounts_map = snapshot.get("accounts_map") or {}
                for _syms in _snap_accounts_map.values():
                    _syms.sort(key=lambda s: (s.get("name") or s.get("id", "")).lower())

                # Before the defaults fill a missing current_return with 0.0.
                _inject_guard_alpha(_state)
                _normalize_frozen_symphonies(snapshot)
                _frozen_stamps = _snapshot_stamps(snapshot, _req_now_et)

                _frozen_env = _dotenv_module.dotenv_values(ENV_FILE_PATH)
                _frozen_live_mode = env_flags.is_true(_frozen_env.get("LIVE_EXECUTION", "False"))
                return jsonify(
                    {
                        "status": "active",
                        "market_state": market_state,
                        "frozen_at": _frozen_stamps["frozen_at"],
                        "trading_day": _frozen_stamps["trading_day"],
                        "snapshot_is_previous_session": _frozen_stamps[
                            "snapshot_is_previous_session"
                        ],
                        "data_as_of": _frozen_stamps["data_as_of"],
                        "state": _state,
                        "bot_state": _state,
                        "live_mode": _frozen_live_mode,
                        "portfolio_strip": _portfolio_strip,
                        "shadow_divergence": sd,
                        "accounts_map": snapshot.get("accounts_map"),
                        "symphonies": [
                            _symphony_card(_id, _e)
                            for _id, _e in _state.items()
                            if isinstance(_e, dict)
                        ],
                        "fleet_correlation_alert": _alert,
                        "meta": _build_meta(
                            _state,
                            market_state=market_state,
                            portfolio_strip=_meta_strip,
                            window_anchor=trading_session.anchor,
                            session_wording_day=trading_session.wording_day,
                        ),
                        **_additive,
                    }
                )

        # No live state — return waiting with market_state context and notice on fresh deploy.
        # AC-DM.3.4: closed + no snapshot + empty state → notice fields included in waiting.
        if view["branch"] == "waiting":
            today_str, _ = _et_trading_day_bounds(_req_now_et)
            try:
                shadow_divergence = database.get_shadow_divergence(today_str)
            except Exception:
                shadow_divergence = {"by_symphony": {}, "portfolio_today": None}
            for sym_id, entry in shadow_divergence["by_symphony"].items():
                entry["name"] = sym_id
            _alert = _active_fleet_alert()
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
            if _meta_strip.get("hist_dates"):
                _wait_strip = {
                    "hist_dates": _meta_strip.get("hist_dates", []),
                    "hist_bot": _meta_strip.get("hist_bot", []),
                    "hist_held": _meta_strip.get("hist_held", []),
                }
            _waiting_body = {
                **waiting_resp,
                "bot_state": _api_state.get("bot_state", {}),
                "meta": _build_meta(
                    {},
                    market_state=market_state,
                    portfolio_strip=_wait_strip,
                    window_anchor=trading_session.anchor,
                    session_wording_day=trading_session.wording_day,
                ),
                **_additive,
            }
            _waiting_body["portfolio_strip"] = _meta_strip
            return jsonify(_waiting_body)

        # Optional ?account=<id> narrows the per-symphony outputs: cards,
        # state, bot_state and symphonies. Dict-valued non-symphony metadata keys
        # (last_market_close_snapshot) are dropped from a filtered state/bot_state,
        # as the shared predicate treats them as non-members; string metadata
        # (date, last_successful_cycle_at, post_mortem_run) stays. portfolio_strip
        # and meta always come from the whole state, so state_data is never narrowed.
        _account_filter = request.args.get("account")

        env_vars = dotenv_values(".env")
        live_mode = env_flags.is_true(env_vars.get("LIVE_EXECUTION", "False"))

        # Seconds until the next engine cycle. The minute scheduler fires at
        # :00 of every minute, so the wait is always wall-clock time to the
        # next minute boundary. Computed directly here — the prior
        # schedule.get_jobs() introspection returned a stale 0 on a live daemon.
        next_run_seconds = _seconds_to_next_minute(datetime.now())

        # AC-4d: name-guard so phantom non-symphony top-level dicts (e.g.
        # last_market_close_snapshot) cannot leak into the cards/standby list.
        # Matches the SSR/meta paths (app.py:433/544) which already require "name".
        symphony_keys = [
            k for k, v in state_data.items() if _is_symphony_in_account(v, _account_filter)
        ]
        for k in symphony_keys:
            sym = state_data[k]
            sym["id"] = k
            sym["normalized_name"] = database.normalize_name(sym.get("name", ""))

        # Build symphonies list for M1 analytics helpers from bot_state.
        # Fields derived: last_percent_change from current_return/100, value from current_value.
        # Composer CR/MDD fields use None default so missing data is distinguishable from 0.0.
        today_str, today_start = _et_trading_day_bounds(_req_now_et)
        # The day the figures belong to: today while the market is open; the session that
        # closed otherwise (a closed market's day does not turn over at midnight).
        figures_day = trading_session.anchor.isoformat()
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
                    "trading_day": figures_day,
                }
            )
            # DE-HELD-BASIS-001 F4 (PR #125 review): no current_return_is_reconstructed
            # key threaded above — analytics.get_symphony_today_change reads the marker
            # from bot_state_entry (the real `s`/state_data[k] dict passed at each call
            # site below, already carrying BL-9's marker) as the PRIMARY source, so this
            # per-symphony sym_dict threading is redundant by construction (removed, was
            # added in the original cycle before the F4 design pivot).

        # Attach last_trigger (today's most-recent) to each symphony for the Status sub-line.
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

        # Attach per-symphony TC/CR/MDD to each sym dict of the response body.
        _today_et = figures_day
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
        # The JS fallback hero freshness signal: the same stamp the strip carries, the newest
        # observation of the completed cycle (never the request time).
        data_as_of = _portfolio_strip.get("data_as_of", _NO_STAMP)

        # PA-M1F-14: shadow_divergence — one lightweight GROUP BY query, not on execution path.
        try:
            shadow_divergence = database.get_shadow_divergence(today_str)
        except Exception:
            shadow_divergence = {"by_symphony": {}, "portfolio_today": None}
        for sym_id, entry in shadow_divergence["by_symphony"].items():
            entry["name"] = (state_data.get(sym_id) or {}).get("name") or sym_id

        _alert = _active_fleet_alert()

        # Inject guard_alpha into triggered symphony entries for dashboard card verdicts.
        _inject_guard_alpha(state_data)

        # Build per-symphony list for card live-refresh (cards-live).
        # Reuses _tc/_cr/_mdd already attached above — no re-computation.
        # Status priority: triggered > para_armed > tp_armed > armed > standby.
        _symphonies_for_cards = [_symphony_card(_k, state_data[_k]) for _k in symphony_keys]

        _narrowed_state = {
            k: v
            for k, v in state_data.items()
            if not _account_filter
            or not isinstance(v, dict)
            or _is_symphony_in_account(v, _account_filter)
        }
        return jsonify(
            {
                "status": "active",
                "market_state": market_state,
                "frozen_at": None,
                "state": _narrowed_state,
                "bot_state": _narrowed_state,
                "live_mode": live_mode,
                "execution_start_time": env_vars.get("EXECUTION_START_TIME", "09:30"),
                "next_run_seconds": next_run_seconds,
                "portfolio_strip": _portfolio_strip,
                "data_as_of": data_as_of,
                "last_successful_cycle_at": state_data.get("last_successful_cycle_at"),
                "shadow_divergence": shadow_divergence,
                "fleet_correlation_alert": _alert,
                "symphonies": _symphonies_for_cards,
                "meta": _build_meta(
                    state_data,
                    next_run_seconds=next_run_seconds,
                    market_state=market_state,
                    portfolio_strip=_meta_strip,
                    window_anchor=trading_session.anchor,
                    session_wording_day=trading_session.wording_day,
                ),
                **_additive,
            }
        )
    except Exception as e:
        _daemon_log.error("api route failed: %s", type(e).__name__)
        _daemon_log.debug("api route failure detail", exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500
    finally:
        try:
            _ro_conn.close()
        except Exception:
            pass
        if _shadow_conn is not None:
            _shadow_conn.close()


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
    series: tuple[list, list, list] | None, window: object, anchor: date | None = None
) -> tuple[list, list, list] | None:
    """Slice a (dates, a, b) series to the CALENDAR cutoff analytics.window_cutoff
    resolves for `window` counted back from `anchor` (the session the figures belong to) — the
    SAME cutoff function /api/strip's compute_windowed_portfolio_strip already canonicalizes
    (AC-5 / MAPERF-03), so a picker click covers the same calendar span on the hero chart, the
    strip, and /api/performance's "ytd" token alike. Without an anchor the cutoff counts back
    from today's UTC date (analytics._window_cutoff_date), as before; every route passes one.

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
        cutoff = (
            analytics.window_cutoff(window, anchor)
            if anchor is not None
            else analytics._window_cutoff_date(window)
        )
    except Exception:
        cutoff = None
    cutoff_iso = cutoff.isoformat() if isinstance(cutoff, date) else None
    idx = [i for i, d in enumerate(dates) if cutoff_iso is None or str(d) >= cutoff_iso]
    return [dates[i] for i in idx], [series_a[i] for i in idx], [series_b[i] for i in idx]


def _hero_chart_body(
    window: str,
    conn: sqlite3.Connection | None = None,
    state: dict | None = None,
    anchor: date | None = None,
) -> dict[str, object]:
    """Return hist_dates/hist_bot/hist_held for the requested time window.

    window values: 30d, 60d, 90d, 125d, ytd, 1y, all

    conn / state: the connection and value weights a frozen response reads, so the chart
    sees the same rows and weights as the poll; omitted, it reads live state and all rows.

    anchor: the ET trading day of the session the chart belongs to; the window counts back from it.

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
        extra = {k: v for k, v in (("conn", conn), ("state", state)) if v is not None}
        bh = analytics.get_portfolio_bot_and_held_daily_returns(days=None, **extra)
        sliced = _slice_series_by_window_cutoff(bh, window, anchor)
        if sliced is not None:
            dates, bot_daily, held_daily = sliced
            # Each series is compounded INDEPENDENTLY into its own cumulative curve.
            bot_series = _compound(bot_daily)
            held_series = _compound(held_daily)
            insufficient = len(dates) < required
            return {
                "hist_dates": dates,
                "hist_bot": bot_series,
                "hist_held": held_series,
                "window": window,
                "source": "shadow_history",
                "insufficient_history": insufficient,
                "available_days": len(dates),
            }
    except Exception as exc:
        _note_build_failure()
        # Exception text can carry paths and data, so only the type reaches the ERROR line.
        _daemon_log.error("get_hero_chart bot/held series failed: %s", type(exc).__name__)
        _daemon_log.debug("get_hero_chart bot/held series detail", exc_info=True)

    return {
        "hist_dates": [],
        "hist_bot": [],
        "hist_held": [],
        "window": window,
        "insufficient_history": True,
        "available_days": 0,
    }


@app.route("/api/hero-chart/<window>")
def get_hero_chart(window):
    """Return hist_dates/hist_bot/hist_held for the requested time window.

    After the close the chart reads the snapshot's rows and weights, like the poll, and its window
    counts back from the session the snapshot closes; a closed market's chart is held per snapshot.
    """
    try:
        live_state = database.load_state() or {}
    except Exception:
        live_state = {}
    now_et = datetime.now(_ET)
    trading_session = _session_view(live_state, get_market_state(now_et), now_et)
    if trading_session.snapshot is not None:
        return jsonify(_closed_hero_body(trading_session, window, now_et))
    # Weights come from the state without the symphonies no longer held; without any such
    # symphony the chart reads the saved state itself, as it always has.
    gone = _no_longer_held_ids(live_state, None, now_et)
    return jsonify(
        _hero_chart_body(
            window, None, _without_symphonies(live_state, gone), trading_session.cutoff_anchor
        )
        if gone
        else _hero_chart_body(window, anchor=trading_session.cutoff_anchor)
    )


# Window tokens accepted by the picker + the windowed-strip route. Lowercase URL
# tokens are the canonical UI form; analytics.compute_windowed_portfolio_strip
# normalizes them internally (30d->30 days, all->lifetime cross-epoch, etc.).
_STRIP_WINDOW_TOKENS = {"30d", "60d", "90d", "125d", "ytd", "1y", "all"}


def _build_windowed_strip(
    token: str, trading_session: _SessionView, bot_state: dict, now_et: datetime
) -> dict:
    """The comparison strip of *token*'s window for *session*; raises when it cannot be computed.

    A closed market's strip is computed from the close snapshot and the rows up to its capture
    moment, exactly as the poll's default-window strip is; an open one from the live state. The
    window counts back from the session's anchor in both.
    """
    trading_day = trading_session.anchor.isoformat()
    frozen_conn = None
    owns_frozen_conn = False
    try:
        if trading_session.snapshot is not None:
            # After the close the window is computed from the snapshot and the rows up to its
            # capture moment, exactly as the poll's default-window strip is.
            frozen_conn, owns_frozen_conn = _closed_conn(trading_session)
            symphonies_list, bot_state = _frozen_symphony_inputs(trading_session.snapshot)
            gone = _no_longer_held_ids(bot_state, frozen_conn, now_et)
            symphonies_list = [s for s in symphonies_list if s.get("id") not in gone]
            bot_state = _without_symphonies(bot_state, gone)
            trading_day = trading_session.snapshot.get("trading_day") or trading_day
        else:
            gone = _no_longer_held_ids(bot_state, None, now_et)
            bot_state = _without_symphonies(bot_state, gone)
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

        if frozen_conn is None:
            # The series reads the saved state for its value weights. It is handed this one only
            # when a no-longer-held entry has to be left out of them; otherwise the call is the
            # one the route has always made.
            weights_state = {"state": bot_state} if gone else {}
            strip = analytics.compute_windowed_portfolio_strip(
                symphonies_list,
                bot_state,
                window=token,
                **trading_session.anchor_kwargs,
                **weights_state,
            )
        else:
            strip = analytics.compute_windowed_portfolio_strip(
                symphonies_list,
                bot_state,
                window=token,
                conn=frozen_conn,
                state=bot_state,
                **trading_session.anchor_kwargs,
            )

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
                if frozen_conn is None:
                    try:
                        _bot_state_dict = database.load_state()
                    except Exception:
                        _bot_state_dict = {}
                    _conn = database.get_connection()
                else:
                    _bot_state_dict = bot_state
                    _conn = frozen_conn
                try:
                    # AC-8 (MAPERF-04): day-filtered to the session's trading day —
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
                    except Exception as _exc:
                        if "no such column" not in str(_exc).lower():
                            raise  # a read fault is a failed build, never the wider answer
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
                    if frozen_conn is None:
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
                _note_build_failure()
        return strip
    finally:
        if frozen_conn is not None and owns_frozen_conn:
            frozen_conn.close()


def _windowed_strip(
    trading_session: _SessionView, token: str, bot_state: dict, now_et: datetime
) -> dict:
    """The strip of *token*'s window: held for a closed market, computed for an open one."""
    return _held_closed(
        trading_session,
        now_et,
        ("strip", token),
        lambda: _build_windowed_strip(token, trading_session, bot_state, now_et),
    )


@app.route("/api/strip/<window>")
@_shares_reads
def get_windowed_strip(window):
    """Return the comparison strip (guard-alpha/CR/MDD/vol) recomputed FOR a window.

    AC-3: the time picker must re-window EVERY hero metric, not just the chart.
    This route threads the selected window token through to
    analytics.compute_windowed_portfolio_strip so the headline guard-alpha VALUE
    (and CR/MDD/vol) reflects the chosen window — 30d/60d/90d/125d/ytd/1y and the
    NEW "all" (All Time, lifetime cross-epoch) token. The returned dict echoes the
    resolved window so the UI label always matches the value (kills the "30d" label
    on an all-time number — F1).

    The window counts back from the session the figures belong to (_session_view), never the
    wall clock; a closed market's strip is held per snapshot.

    Read-only: builds the symphony list from the state DB; never reruns the engine.
    """
    token = (window or "").lower()
    if token not in _STRIP_WINDOW_TOKENS:
        return jsonify({"error": "unknown window token"}), 404

    now_et = datetime.now(_ET)
    try:
        bot_state = database.load_state() or {}
    except Exception:
        bot_state = {}
    trading_session = _session_view(bot_state, get_market_state(now_et), now_et)
    try:
        strip = _windowed_strip(trading_session, token, bot_state, now_et)
    except Exception:
        _daemon_log.error("get_windowed_strip failed for window=%s", token, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500
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
    every symphony in database.load_state() that is still held (the shared
    no-longer-held rule leaves the others out), returns a "replay" sample
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
    # A symphony no longer held has no entry: every other surface leaves it out through this one
    # shared rule, and the page's "replay samples unavailable N of M" counts the ids returned here.
    no_longer_held = _no_longer_held_ids(bot_state_dict, None, datetime.now(_ET))

    for sym_id, sym_data in bot_state_dict.items():
        if not _is_symphony_state_entry(sym_data) or sym_id in no_longer_held:
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
    # The window counts back from the session the figures belong to, never the wall clock.
    trading_session = _current_session()[0]
    _cutoff = _window_cutoff_of(trading_session, resolved_window)
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
                _today_et = trading_session.anchor.isoformat()
                try:
                    rows = conn.execute(
                        "SELECT t.symphony_id, t.at_return, "
                        "  (SELECT current_return FROM shadow_history "
                        "   WHERE symphony_id = t.symphony_id ORDER BY ts_utc DESC LIMIT 1) "
                        "FROM exit_triggers t WHERE substr(t.ts_et, 1, 10) = ?",
                        (_today_et,),
                    ).fetchall()
                except Exception as _exc:
                    if "no such column" not in str(_exc).lower():
                        raise  # a read fault answers "no guard events yet" via the outer except
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


def _join_retirement_approval_status(
    recs: list[dict], *, id_key: str = "candidate_id", absent_status: str | None = "pending"
) -> None:
    """Stamp approval_status onto each dict in recs in place, read FRESH from
    the mutable retirement_decisions table on every call -- never cached.

    The one join of the decisions table, shared by the recommendation list
    (id_key "candidate_id", a missing decision reads "pending") and the tenure
    review entries (id_key "symphony_id", a missing decision reads None, so an
    undecided symphony is distinguishable from one stored as pending). Used by
    _fetch_retirement_recommendations (backs GET /api/retirement-
    recommendations and the AI Advisor panel prefetch) and _fetch_tenure_review,
    so the API and the rendered panel always agree on decision state. A
    database.get_retirement_decisions() read failure leaves every row at
    absent_status rather than raising. Never raises.

    A no-op on an empty list, BEFORE calling database.get_retirement_decisions(),
    which avoids a DB read on the honest-empty-state common case.
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
        rec_id = rec.get(id_key)
        # A stored id that is not text (a list, a dict) cannot key the lookup.
        decision = decisions_by_id.get(rec_id) if isinstance(rec_id, str) else None
        rec["approval_status"] = (decision or {}).get("approval_status") or absent_status


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
        # A stored id that is not usable text (not text, empty, blank, a number, a boolean,
        # a list, a dict) cannot be resolved: that field keeps whatever name it carries;
        # the other field and the other rows are unaffected.
        if isinstance(rec.get("candidate_id"), str) and rec["candidate_id"].strip():
            rec["candidate_name"] = _resolved_or_persisted_or_hash(
                rec["candidate_id"], rec.get("candidate_name")
            )
        if isinstance(rec.get("sibling_id"), str) and rec["sibling_id"].strip():
            rec["sibling_name"] = _resolved_or_persisted_or_hash(
                rec["sibling_id"], rec.get("sibling_name")
            )


def _fetch_retirement_last_run() -> dict | None:
    """Return the latest RETIREMENT_RUN_SUMMARY row's raw_response dict, or
    None when no run has been recorded yet (Cycle 2e, AC-5).

    Read-only (database.get_advisor_observations_for_role opens a read-only
    connection). Deliberately RAISES on a DB-read failure instead of swallowing
    it -- the same contract as _fetch_retirement_recommendations: each caller
    degrades on its own terms (the API route to last_run null, the panel to its
    "Not run yet" block), so a read failure is never mistaken for "never run"
    inside this helper. This is the latest run of ANY status (what the
    "last run" block shows); the run the cards are scoped to is resolved
    separately by _fetch_retirement_cards_run. A newest row whose raw_response is not a dict (a
    hand-written/corrupt row) is also None -- there is no usable summary.
    """
    rows = database.get_advisor_observations_for_role(_RETIREMENT_RUN_SUMMARY_ROLE, limit=1)
    if not rows:
        return None
    raw = rows[0].get("raw_response")
    return raw if isinstance(raw, dict) else None


# What the Retirement tab prints for a value its summary does not carry: never 0.
_RETIREMENT_MISSING_TEXT = "—"


def _retirement_exclusion_reason_sentence(reason: object) -> str:
    """The sentence the Retirement tab prints for why a symphony was left out of a run.

    `reason` is the literal advisors.retirement_recommender stored in the run summary;
    its literals are read from that module (a lazy import) so there is one spelling of
    each. A reason the mapping does not know reads "Excluded: <reason>" (the text is
    autoescaped where it is rendered); an empty or non-text reason reads "Reason not
    recorded." Never raises.
    """
    if not isinstance(reason, str) or not reason.strip():
        return "Reason not recorded."
    try:
        from advisors import retirement_recommender as _rr  # noqa: PLC0415 -- CC-2 lazy

        fetch_prefix = _rr._REASON_FETCH_ERROR_PREFIX
        if reason == _rr._REASON_NO_ACCOUNT:
            return "No Composer account is recorded for this symphony."
        if reason == _rr._REASON_SERIES_TOO_SHORT:
            return "Its Composer series has fewer than 2 daily returns."
        if reason == _rr._REASON_SHADOW_UNAVAILABLE:
            return "No shadow history is available for it."
        if reason.startswith(fetch_prefix):
            code = reason[len(fetch_prefix) :].strip()
            if code:
                return f"Its Composer series could not be fetched ({code})."
            return "Its Composer series could not be fetched."
    except Exception:
        pass
    return f"Excluded: {reason}"


def _retirement_summary_count(value: object) -> int | None:
    """A stored count as an int, or None when it is absent or not a whole number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _retirement_summary_count_text(value: object) -> str:
    count = _retirement_summary_count(value)
    return str(count) if count is not None else _RETIREMENT_MISSING_TEXT


def _build_retirement_summary_tiles(last_run: object) -> list[dict]:
    """The five stat tiles of a status "ok" run summary, in display order.

    Each tile is {"label", "value", "detail"} (all text; detail None when there is none).
    Every value is derived from the summary's own keys, the screen threshold and the
    stress-gate minimum included; a key that is absent or null makes its tile's value
    the dash, never 0, and the Blocked total is the dash when any of its four gate
    counts is missing. Any other status (or no summary) has no tiles. Pure: reads
    `last_run`, changes nothing.
    """
    if not isinstance(last_run, dict) or last_run.get("status") != "ok":
        return []
    missing = _RETIREMENT_MISSING_TEXT

    def pair_text(first_key: str, second_key: str, template: str) -> str:
        first = _retirement_summary_count(last_run.get(first_key))
        second = _retirement_summary_count(last_run.get(second_key))
        if first is None or second is None:
            return missing
        return template.format(first, second)

    threshold = last_run.get("screen_threshold")
    threshold_ok = isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
    threshold_ok = threshold_ok and math.isfinite(threshold)
    pairs_label = f"Pairs screened at {threshold} or above" if threshold_ok else "Pairs screened"

    gate_keys = (
        ("too uncertain", "blocked_uncertainty"),
        ("too few worst days", "blocked_stress_too_thin"),
        ("apart on worst days", "blocked_stress_below_threshold"),
        ("no score to compare", "blocked_no_candidate"),
    )
    gates = [_retirement_summary_count(last_run.get(key)) for _words, key in gate_keys]
    blocked_total = missing if any(g is None for g in gates) else str(sum(gates))
    blocked_detail = ", ".join(
        f"{words} {_retirement_summary_count_text(last_run.get(key))}" for words, key in gate_keys
    )
    too_thin = _retirement_summary_count(last_run.get("blocked_stress_too_thin"))
    if too_thin is not None and too_thin > 0:
        blocked_detail += (
            ". The worst-days check needs at least "
            f"{_retirement_summary_count_text(last_run.get('stress_gate_min_aligned_days'))}"
            " days on which both symphonies have a return."
        )

    return [
        {
            "label": "Analysed",
            "value": pair_text("symphonies_with_series", "roster_size", "{} of {}"),
            "detail": None,
        },
        {
            "label": "History",
            "value": pair_text("series_days_min", "series_days_max", "{} to {} days"),
            "detail": None,
        },
        {
            "label": pairs_label,
            "value": pair_text("pairs_screened", "pairs_evaluated", "{} of {}"),
            "detail": None,
        },
        {"label": "Blocked", "value": blocked_total, "detail": blocked_detail},
        {
            "label": "Recommendations",
            "value": _retirement_summary_count_text(last_run.get("recommendations")),
            "detail": None,
        },
    ]


def _build_retirement_pointer(
    last_run: object, recommendations: object, review_entries: object = ()
) -> dict:
    """The one line on the Overview that points at the Retirement tab: {"text": ...}.

    Never run: "Retirement: not run yet". A good run: its Eastern time, its recommendation
    count and how many decisions are still pending. A run that did not finish names its
    state (the status, underscores as spaces) and its time. Pending counts every decision
    that waits: recommender cards whose approval_status is "pending" plus the tenure
    review's retire candidates with no decision yet or a pending one; an approved or
    rejected item, and a review entry that is not a retire candidate, do not count. Pure:
    reads only what the page already holds, issues no request.
    """
    if not isinstance(last_run, dict):
        return {"text": "Retirement: not run yet (use Run recommender)"}
    stamp = analytics.format_et_stamp(last_run.get("run_at"))
    status = last_run.get("status")
    if status != "ok":
        state = (
            status.replace("_", " ") if isinstance(status, str) and status.strip() else "unknown"
        )
        return {"text": f"Retirement: last run {state}, {stamp}"}
    pending = sum(
        1
        for rec in (recommendations if isinstance(recommendations, (list, tuple)) else ())
        if isinstance(rec, dict) and rec.get("approval_status") == "pending"
    )
    pending += sum(
        1
        for entry in (review_entries if isinstance(review_entries, (list, tuple)) else ())
        if isinstance(entry, dict)
        and entry.get("verdict") == "retire_candidate"
        and entry.get("approval_status") in (None, "pending")
    )
    count = _retirement_summary_count(last_run.get("recommendations"))
    noun = "recommendation" if count == 1 else "recommendations"
    return {
        "text": (
            f"Retirement: last run {stamp}, "
            f"{_retirement_summary_count_text(last_run.get('recommendations'))} {noun}, "
            f"{pending} pending"
        )
    }


def _retirement_resolve_excluded_symphonies(
    last_run: dict | None, bot_state_getter: Callable[[], dict]
) -> list[dict]:
    """Return [{"symphony_id", "name", "reason", "reason_text"}, ...] for the
    "excluded from this run" list of the panel's last-run block (Cycle 2e, AC-6).

    `last_run["symphonies_without_series"]` carries Composer hashes; the panel
    shows the operator's display name with the hash as the honest fallback for
    a symphony no longer in bot_state, resolved through the same shared
    advisors.frontrunner_builder.resolve_incumbent_display_name helper the
    recommendation cards use. `reason` is passed through verbatim and
    `reason_text` is the sentence for it (_retirement_exclusion_reason_sentence);
    the template prints `reason_text` and autoescapes it. The stored summary is
    never changed. bot_state is read through `bot_state_getter` only when
    there is something to resolve, so a run that excluded nothing costs no
    load_state(). Malformed entries are skipped; never raises (any failure
    degrades to []).
    """
    try:
        entries = last_run.get("symphonies_without_series") if isinstance(last_run, dict) else None
        if not isinstance(entries, list) or not entries:
            return []
        try:
            from advisors.frontrunner_builder import (  # noqa: PLC0415 -- CC-2 lazy
                resolve_incumbent_display_name as _resolve_name,
            )
        except Exception:
            _resolve_name = None
        bot_state = bot_state_getter()
        excluded: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symphony_id = entry.get("symphony_id")
            if not isinstance(symphony_id, str) or not symphony_id:
                continue
            name = symphony_id
            if _resolve_name is not None:
                resolved = _resolve_name(bot_state, symphony_id)
                if isinstance(resolved, str) and resolved:
                    name = resolved
            reason = entry.get("reason")
            reason = reason if isinstance(reason, str) else ""
            excluded.append(
                {
                    "symphony_id": symphony_id,
                    "name": name,
                    "reason": reason,
                    "reason_text": _retirement_exclusion_reason_sentence(reason),
                }
            )
        return excluded
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Correlations tab: the matrix of the latest successful retirement run
#
# The tab renders the pairs the retirement recommender stored in its run summary
# (the realized series). Nothing here fetches a series or recomputes a
# correlation: the render reads the stored summary and shapes it.
# ---------------------------------------------------------------------------

# Display bound between the neutral and the moderate (warn) correlation bands and,
# negated, the start of the diversifying band. A display choice of the matrix, not
# a screen or a gate: the screen threshold the high band starts at is read from the
# run's own summary.
_CORRELATION_MODERATE_BOUND = 0.40

# Inline fill strength (percent of the band token mixed over the page) of a filled
# cell: linear in the printed |r| from the moderate bound (MIN) to 1.00 (MAX). The
# cap keeps the ink-token number at 4.5:1 contrast or better over the strongest mix
# of each signal token in both themes (computed from static/tokens.css: the weakest
# pairing is the dark theme's positive token, 5.2:1 at 45%).
_CORRELATION_FILL_PCT_MIN = 15.0
_CORRELATION_FILL_PCT_MAX = 45.0

_CORRELATION_UNDEFINED_TEXT = "undefined"
_CORRELATION_FILLED_BANDS = ("danger", "warn", "diversifying")
_CORRELATION_NEXT_RUN_TEXT = "nightly at 03:45 UTC, or Run recommender on the Retirement tab"
_CORRELATION_NEVER_RUN_TEXT = (
    "No realized series yet: the matrix fills after the next retirement run "
    f"({_CORRELATION_NEXT_RUN_TEXT})."
)
_CORRELATION_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _retirement_correlation_text(r: object) -> str:
    """The correlation as a cell prints it: signed, two decimals; the word
    "undefined" for a missing or non-finite value."""
    if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r):
        return _CORRELATION_UNDEFINED_TEXT
    return f"{r:+.2f}"


def _retirement_correlation_band(text: str, screen_threshold: float) -> str:
    """The band of a PRINTED correlation (a stored 0.6499 prints +0.65 and bands
    with it): danger at or above ``screen_threshold``, warn from the moderate
    bound up to it, diversifying at or below the negated moderate bound, neutral
    between, undefined for the word. A negative value is never danger."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return "undefined"
    if not math.isfinite(value):
        return "undefined"
    if value >= screen_threshold:
        return "danger"
    if value >= _CORRELATION_MODERATE_BOUND:
        return "warn"
    if value <= -_CORRELATION_MODERATE_BOUND:
        return "diversifying"
    return "neutral"


def _retirement_correlation_fill_pct(text: str) -> float:
    """Fill strength of a filled cell: linear in the printed size |r| between the
    two fill constants, the same for +x and -x."""
    try:
        size = abs(float(text))
    except (TypeError, ValueError):
        return _CORRELATION_FILL_PCT_MIN
    if not math.isfinite(size):
        return _CORRELATION_FILL_PCT_MIN
    low = _CORRELATION_MODERATE_BOUND
    fraction = (min(max(size, low), 1.0) - low) / (1.0 - low)
    return (
        _CORRELATION_FILL_PCT_MIN
        + (_CORRELATION_FILL_PCT_MAX - _CORRELATION_FILL_PCT_MIN) * fraction
    )


def _retirement_correlation_thin_level(n_obs: int, uncertainty_min: int, stress_min: int) -> str:
    """Which recommender gate a pair's aligned days cannot pass: "uncertainty"
    below the uncertainty minimum, else "stress" below the stress minimum, else
    "none". Both minimums come from the run's summary."""
    if n_obs < uncertainty_min:
        return "uncertainty"
    if n_obs < stress_min:
        return "stress"
    return "none"


def _retirement_correlation_is_date(value: object) -> bool:
    """True for a stored ISO calendar date (YYYY-MM-DD) that is a real date."""
    if not isinstance(value, str) or not _CORRELATION_ISO_DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _retirement_series_through(run: object) -> str | None:
    """The latest ``window_last`` among a run summary's ``series_windows``, as
    stored (a calendar date, never put through the time formatter), or None for a
    run that stored no windows."""
    windows = run.get("series_windows") if isinstance(run, dict) else None
    if not isinstance(windows, dict):
        return None
    lasts = [
        w["window_last"]
        for w in windows.values()
        if isinstance(w, dict) and _retirement_correlation_is_date(w.get("window_last"))
    ]
    return max(lasts) if lasts else None


def _retirement_correlation_count(value: object) -> int | None:
    """A stored non-negative whole number, or None."""
    count = _retirement_summary_count(value)
    return count if count is not None and count >= 0 else None


def _retirement_correlation_run_stamp(run: object) -> str | None:
    """The run's Eastern time, or None when it recorded none."""
    run_at = run.get("run_at") if isinstance(run, dict) else None
    if not isinstance(run_at, str) or not run_at.strip():
        return None
    stamp = analytics.format_et_stamp(run_at)
    return None if stamp == analytics.ET_STAMP_MISSING else stamp


def _retirement_correlation_state_view(state: str, title: str, lines: list[str]) -> dict:
    return {"state": state, "title": title, "lines": lines}


def _retirement_correlation_no_matrix_view(last_run: dict) -> dict:
    """The state block for a latest run that holds no matrix (a failed run, and no
    earlier successful one)."""
    stamp = _retirement_correlation_run_stamp(last_run) or "time not recorded"
    status = last_run.get("status")
    if status in ("series_unavailable", "insufficient_symphonies"):
        lines = [
            f"The latest run ({stamp}) found fewer than two symphonies with a usable series.",
            "Symphonies analysed: "
            f"{_retirement_summary_count_text(last_run.get('symphonies_with_series'))} of "
            f"{_retirement_summary_count_text(last_run.get('roster_size'))}.",
        ]
    elif status == "internal_error":
        error_type = last_run.get("error_type")
        detail = f" ({error_type})" if isinstance(error_type, str) and error_type.strip() else ""
        lines = [f"The latest run ({stamp}) ended in an internal error{detail}."]
    else:
        lines = [f"The latest run ({stamp}) did not produce a matrix."]
    lines.append(f"The matrix fills after the next retirement run ({_CORRELATION_NEXT_RUN_TEXT}).")
    return _retirement_correlation_state_view("no_matrix", "No matrix exists yet", lines)


def _retirement_correlation_parse_pair(record: object) -> dict | None:
    """One stored pair, validated, or None for a record that is not a pair."""
    if not isinstance(record, dict):
        return None
    sym_a, sym_b = record.get("sym_a"), record.get("sym_b")
    if not (isinstance(sym_a, str) and sym_a and isinstance(sym_b, str) and sym_b):
        return None
    if sym_a == sym_b:
        return None
    correlation = record.get("correlation")
    if correlation is not None:
        if isinstance(correlation, bool) or not isinstance(correlation, (int, float)):
            return None
        if not math.isfinite(correlation) or abs(correlation) > 1.0:
            return None
    n_obs = _retirement_correlation_count(record.get("n_obs"))
    if n_obs is None:
        return None
    window_first, window_last = record.get("window_first"), record.get("window_last")
    for edge in (window_first, window_last):
        if edge is not None and not _retirement_correlation_is_date(edge):
            return None
    return {
        "correlation": correlation,
        "n_obs": n_obs,
        "window_first": window_first,
        "window_last": window_last,
    }


def _retirement_correlation_matrix_state(
    last_run: dict | None, matrix_run: dict, bot_state_getter: Callable[[], dict]
) -> dict:
    """The matrix view of ``matrix_run`` (its summary has a ``pairs`` list).

    Raises ValueError when the stored run cannot draw a truthful matrix (a missing
    or malformed threshold, minimum, window, or a pair missing for two analysed
    symphonies); the caller turns that into the unavailable state. Never partial.
    """
    threshold = matrix_run.get("screen_threshold")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0.0 < threshold <= 1.0
    ):
        raise ValueError("screen threshold")
    uncertainty_min = _retirement_correlation_count(matrix_run.get("uncertainty_min_aligned_days"))
    stress_min = _retirement_correlation_count(matrix_run.get("stress_gate_min_aligned_days"))
    if uncertainty_min is None or stress_min is None:
        raise ValueError("gate minimums")

    windows = matrix_run.get("series_windows")
    if not isinstance(windows, dict) or len(windows) < 2:
        raise ValueError("series windows")
    for sym, w in windows.items():
        if not (isinstance(sym, str) and sym and isinstance(w, dict)):
            raise ValueError("series window entry")
        dates = (w.get("window_first"), w.get("window_last"))
        dates += (w.get("series_first"), w.get("series_last"))
        counts = (w.get("window_days"), w.get("series_points"))
        if not all(_retirement_correlation_is_date(d) for d in dates):
            raise ValueError("series window dates")
        if any(_retirement_correlation_count(c) is None for c in counts):
            raise ValueError("series window counts")

    stored: dict[tuple[str, str], dict] = {}
    for record in matrix_run["pairs"]:
        pair = _retirement_correlation_parse_pair(record)
        if pair is not None:
            key = tuple(sorted((record["sym_a"], record["sym_b"])))
            stored.setdefault(key, pair)

    # Names: the live display name, the hash when the symphony left the live state.
    try:
        bot_state = bot_state_getter()
    except Exception:
        bot_state = {}
    try:
        from advisors.frontrunner_builder import (  # noqa: PLC0415 -- CC-2 lazy
            resolve_incumbent_display_name as _resolve_name,
        )
    except Exception:
        _resolve_name = None
    raw_names: dict[str, str] = {}
    for sym in windows:
        resolved = _resolve_name(bot_state, sym) if _resolve_name is not None else sym
        raw_names[sym] = resolved if isinstance(resolved, str) and resolved.strip() else sym
    order = sorted(windows, key=lambda sym: (raw_names[sym], sym))
    names = {sym: " ".join(raw_names[sym].split()) for sym in order}

    max_fill = f"{_CORRELATION_FILL_PCT_MAX:.1f}"
    axes: list[dict] = []
    for number, row_sym in enumerate(order, start=1):
        w = windows[row_sym]
        row_name = names[row_sym]
        aria = (
            f"{row_name}. Matrix window: {w['window_first']} to {w['window_last']} "
            f"({w['window_days']} returns). Full series: {w['series_first']} to "
            f"{w['series_last']} ({w['series_points']} points)."
        )
        cells: list[dict] = []
        for col_sym in order:
            if col_sym == row_sym:
                cells.append({"diagonal": True})
                continue
            key = tuple(sorted((row_sym, col_sym)))
            pair = stored.get(key)
            if pair is None:
                raise ValueError("pair missing")
            text = _retirement_correlation_text(pair["correlation"])
            band = _retirement_correlation_band(text, threshold)
            thin = _retirement_correlation_thin_level(pair["n_obs"], uncertainty_min, stress_min)
            title = f"{row_name} and {names[col_sym]}: {text}, {pair['n_obs']} aligned days"
            if pair["n_obs"] > 0 and pair["window_first"] and pair["window_last"]:
                title += f", {pair['window_first']} to {pair['window_last']}"
            cells.append(
                {
                    "diagonal": False,
                    "sym_a": key[0],
                    "sym_b": key[1],
                    "text": text,
                    "band": band,
                    "thin": thin,
                    "n_obs": pair["n_obs"],
                    "title": title,
                    "fill_pct": (
                        f"{_retirement_correlation_fill_pct(text):.1f}"
                        if band in _CORRELATION_FILLED_BANDS
                        else None
                    ),
                }
            )
        axes.append(
            {
                "number": number,
                "sym": row_sym,
                "name": row_name,
                "row_aria": aria,
                "row_title": f"{aria} {row_sym}",
                "col_aria": f"{row_name} (column {number})",
                "col_title": f"{row_name} ({row_sym})" if row_name != row_sym else row_name,
                "series_first": w["series_first"],
                "series_last": w["series_last"],
                "series_points": w["series_points"],
                "cells": cells,
            }
        )

    run_stamp = _retirement_correlation_run_stamp(matrix_run)
    through = _retirement_series_through(matrix_run)
    run_part = f"run {run_stamp}" if run_stamp else "run time not recorded"
    basis = matrix_run.get("basis_label")

    same_run = last_run is matrix_run or (
        isinstance(last_run, dict)
        and last_run.get("run_id") is not None
        and last_run.get("run_id") == matrix_run.get("run_id")
    )
    note = None
    if not same_run:
        note = (
            f"From the run of {run_stamp or 'an unrecorded time'}; the latest run did not complete."
        )

    threshold_text = f"{threshold:.2f}"
    moderate_text = f"{_CORRELATION_MODERATE_BOUND:.2f}"
    legend_bands = [
        {"band": "danger", "text": f"High: {threshold_text} and above", "fill_pct": max_fill},
        {
            "band": "warn",
            "text": f"Moderate: {moderate_text} up to {threshold_text}",
            "fill_pct": max_fill,
        },
        {
            "band": "neutral",
            "text": f"Weak: -{moderate_text} to {moderate_text}",
            "fill_pct": None,
        },
        {
            "band": "diversifying",
            "text": f"Diversifying: -{moderate_text} and below",
            "fill_pct": max_fill,
        },
    ]
    legend_thin = [
        {
            "level": "uncertainty",
            "text": f"fewer than {uncertainty_min} aligned days: the uncertainty gate cannot pass",
        },
        {
            "level": "stress",
            "text": f"fewer than {stress_min} aligned days: the stress gate cannot pass",
        },
    ]

    excluded = []
    for entry in _retirement_resolve_excluded_symphonies(matrix_run, lambda: bot_state):
        name = " ".join(str(entry["name"]).split()) or entry["symphony_id"]
        excluded.append(
            {
                "symphony_id": entry["symphony_id"],
                "text": f"Not in the matrix: {name}. {entry['reason_text']}",
            }
        )

    return {
        "state": "matrix",
        "basis": basis if isinstance(basis, str) and basis.strip() else "basis not recorded",
        "as_of": f"Series through {through} · {run_part}",
        "earlier_run_note": note,
        "axes": axes,
        "legend_bands": legend_bands,
        "legend_thin": legend_thin,
        "excluded": excluded,
    }


def _retirement_correlation_matrix_view(
    last_run: dict | None,
    cards_run: dict | None,
    *,
    read_failed: bool,
    bot_state_getter: Callable[[], dict],
) -> dict:
    """What the Correlations panel renders, read from the stored retirement runs.

    ``cards_run`` (the newest successful run) is the matrix run; ``last_run`` (the
    newest run of any status) only decides the earlier-run note and the failed-run
    message. The result's ``state`` is "matrix", or one state block:
    "never_run" (no run), "needs_newer_run" (the matrix run stored no pairs),
    "no_matrix" (only failed runs), or "unavailable" (a summary read raised, or
    the stored run cannot draw a truthful matrix). Never raises, reads no series
    and makes no request; ``bot_state_getter`` is called only when a matrix is
    drawn (display names).
    """
    unavailable = _retirement_correlation_state_view(
        "unavailable",
        "Correlations unavailable",
        ["The stored retirement run could not be read, so the matrix is unavailable."],
    )
    try:
        if read_failed:
            return unavailable
        if not isinstance(cards_run, dict):
            if not isinstance(last_run, dict):
                return _retirement_correlation_state_view(
                    "never_run", "No matrix yet", [_CORRELATION_NEVER_RUN_TEXT]
                )
            return _retirement_correlation_no_matrix_view(last_run)
        if not isinstance(cards_run.get("pairs"), list):
            stamp = _retirement_correlation_run_stamp(cards_run) or "time not recorded"
            analysed = cards_run.get("symphonies_with_series")
            if isinstance(analysed, int) and not isinstance(analysed, bool) and analysed >= 0:
                count_text = f"{analysed} {'symphony' if analysed == 1 else 'symphonies'}"
            else:
                count_text = "its symphonies"
            # The run did analyse series; what it lacks is the stored pairs a matrix is drawn from.
            return _retirement_correlation_state_view(
                "needs_newer_run",
                "A newer run is needed",
                [
                    f"The latest successful run ({stamp}) analysed {count_text} but stored no "
                    "pairs, so it cannot draw a matrix.",
                    f"A newer run will ({_CORRELATION_NEXT_RUN_TEXT}).",
                ],
            )
        return _retirement_correlation_matrix_state(last_run, cards_run, bot_state_getter)
    except Exception:
        return unavailable


# Sentinel for the `last_run` parameter of _fetch_retirement_cards_run and the
# `cards_run` keyword of _fetch_retirement_recommendations: "the caller did not
# supply one -- look it up yourself". An explicit None is a real value ("no
# usable summary") and must take the legacy path WITHOUT a second lookup, so it
# cannot double as the default.
_RETIREMENT_LAST_RUN_UNSET = object()

# How many of the newest RETIREMENT_RUN_SUMMARY rows _fetch_retirement_cards_run
# scans for the latest successful run. One row per night (plus any Run-now
# clicks), so this reaches back over a year of consecutive failed nights; a
# longer outage than that degrades to the legacy calendar-date filter.
_RETIREMENT_CARDS_RUN_SCAN_LIMIT = 365


def _fetch_retirement_cards_run(last_run: dict | None = _RETIREMENT_LAST_RUN_UNSET) -> dict | None:
    """Return the summary dict of the run the recommendation CARDS are scoped
    to: the NEWEST RETIREMENT_RUN_SUMMARY whose status is "ok" AND which has a
    truthy run_id -- or None when there is none (never run, only failed
    summaries, hand-written summaries without a run_id): the caller then uses
    the legacy latest-calendar-date filter (Cycle 2e, AC-5b).

    This is deliberately NOT "the latest summary of any status": a failed night
    (series_unavailable / internal_error / insufficient_symphonies) persisted no
    recommendation rows, so scoping the cards to it would hide the last good
    run's cards -- including operator-APPROVED ones and their wind-down
    checklists -- behind a Composer outage. An ok run that found nothing IS
    authoritative (it is a successful look at the fleet), so it hides earlier
    runs' cards, also across later failed nights. An ok summary without a
    run_id is skipped (it cannot scope anything).

    `last_run` is the caller's already-read latest summary: when that is itself
    ok with a run_id it is, by definition, the newest such row and is returned
    without a second read (the common ok-then-ok case). Otherwise the newest
    _RETIREMENT_CARDS_RUN_SCAN_LIMIT summaries are scanned. Read-only. Raises on
    a DB-read failure -- each caller degrades to the legacy path / null.
    """
    if isinstance(last_run, dict) and last_run.get("status") == "ok" and last_run.get("run_id"):
        return last_run
    rows = database.get_advisor_observations_for_role(
        _RETIREMENT_RUN_SUMMARY_ROLE, limit=_RETIREMENT_CARDS_RUN_SCAN_LIMIT
    )
    for row in rows:
        raw = row.get("raw_response")
        if isinstance(raw, dict) and raw.get("status") == "ok" and raw.get("run_id"):
            return raw
    return None


def _fetch_retirement_recommendations(
    limit: int | None = None,
    bot_state_getter: Callable[[], dict] | None = None,
    review_run_id: str | None = None,
    review_unreadable: bool = False,
    *,
    cards_run: dict | None = _RETIREMENT_LAST_RUN_UNSET,
    include_review_rows: bool = True,
) -> list[dict]:
    """Return the persisted RETIREMENT_RECOMMENDATION raw_response dicts of the latest run(s).

    Two producers write this role, and each scopes its rows to its own run; a row
    is returned at most once.

    Tenure review: with review_run_id (the newest completed review run), the rows
    whose own review_run_id equals it are returned -- unless include_review_rows
    is False, which the panel passes because it renders those rows as review
    entries and asks only for the others. review_unreadable (a review run row is
    stored but cannot be read, so which rows belong to it is unknown) returns no
    review rows. A known review run, readable or not, also switches off the
    latest-calendar-date fallback described below.

    Recommender run (Cycle 2e, retirement-visibility AC-5, revised by AC-5b): when
    _fetch_retirement_cards_run finds a latest successful (status "ok", truthy
    run_id) run, ONLY the recommendation rows stamped with that run's run_id
    are returned -- so a later successful run that finds nothing (a 14:00 "Run
    now" with no pairs) correctly empties the panel instead of leaving an
    earlier run's cards on it, while a later FAILED run (series unavailable,
    internal error, too few symphonies) never hides the last good run's cards
    (approved ones included). `cards_run` lets a caller that already resolved
    that summary (the panel prefetch and the API route, which also render it)
    pass that SAME snapshot in so the cards and the page can never disagree
    about which run they come from and no second lookup is issued; omitted, it
    is resolved here. An explicit None means "no usable successful run" (no
    re-read).

    With neither a review run nor a successful recommender run, the
    pre-Cycle-2e latest-calendar-date behavior described below is kept
    unchanged, except that rows stamped with a review_run_id are never listed
    there: with no completed review run to name, they may be the remains of a
    run that failed.

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
    if cards_run is _RETIREMENT_LAST_RUN_UNSET:
        try:
            cards_run = _fetch_retirement_cards_run()
        except Exception:
            cards_run = None  # a failed summary read degrades to the legacy path
    rows = database.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=limit)
    run_id = cards_run.get("run_id") if isinstance(cards_run, dict) else None
    review_rows_wanted = include_review_rows and review_run_id is not None
    review_known = review_run_id is not None or review_unreadable
    out: list[dict] = []
    if run_id or review_rows_wanted:
        # A completed review run and a successful recommender run each own the
        # rows stamped with their own id, so a nightly and a later on-demand run
        # never both show, and rows of a run that never completed or of the
        # pre-review nights stay hidden.
        for row in rows:
            raw = row.get("raw_response")
            if not isinstance(raw, dict):
                continue
            if (review_rows_wanted and raw.get("review_run_id") == review_run_id) or (
                run_id and raw.get("run_id") == run_id
            ):
                out.append(raw)
    elif not review_known:
        # Rows stamped with a review_run_id belong to a run; with no completed run row
        # to name, they may be the remains of a run that failed, so they are not listed.
        dated_rows = [
            row
            for row in rows
            if row.get("created_at")
            and not (
                isinstance(row.get("raw_response"), dict) and "review_run_id" in row["raw_response"]
            )
        ]
        if not dated_rows:
            return []
        latest_date = max(row["created_at"][:10] for row in dated_rows)
        for row in dated_rows:
            if row["created_at"][:10] != latest_date:
                continue
            raw = row.get("raw_response")
            if isinstance(raw, dict):
                out.append(raw)
    _join_retirement_approval_status(out)
    _refresh_retirement_display_names(out, bot_state_getter=bot_state_getter)
    return out


def _format_eastern_run_time(completed_at: object) -> str | None:
    """Return completed_at as "YYYY-MM-DD HH:MM" US Eastern, or None when it is not a
    readable instant. Read through analytics.format_et_stamp, the one conversion of a
    stored time to Eastern; a stamp without an offset is read as UTC, the zone the
    review stores. Never raises."""
    stamp = analytics.format_et_stamp(completed_at)
    suffix = analytics.ET_STAMP_SUFFIX
    return stamp[: -len(suffix)] if stamp.endswith(suffix) else None


_NOT_RECORDED_TEXT = "not recorded"
_TIME_NOT_RECORDED_TEXT = "time not recorded"


@app.template_filter("tenure_text")
def _tenure_text(value: object) -> str:
    """A stored text as stored, or "not recorded" when it is absent or not a text, so the
    panel never prints Python's None or a stored list."""
    return value if isinstance(value, str) and value.strip() else _NOT_RECORDED_TEXT


@app.template_filter("tenure_number")
def _tenure_number(value: object) -> str:
    """A finite stored number to two decimals, else "not recorded" (never nan, inf or True)."""
    if not _is_finite_number(value):
        return _NOT_RECORDED_TEXT
    # An int is printed by its digits: float formatting overflows for a huge one.
    if isinstance(value, int):
        return f"{value}.00"
    text = f"{value:.2f}"
    # A value that rounds to zero prints 0.00, never -0.00.
    return "0.00" if text == "-0.00" else text


@app.template_filter("tenure_whole")
def _tenure_whole(value: object) -> str:
    """A stored whole number as an integer, else "not recorded" (2.5 is never truncated)."""
    return str(int(value)) if _is_whole_number(value) else _NOT_RECORDED_TEXT


@app.template_filter("tenure_eastern")
def _tenure_eastern(value: object) -> str:
    """A stored timestamp as "YYYY-MM-DD HH:MM ET", else "time not recorded"."""
    stamp = _format_eastern_run_time(value)
    return f"{stamp} ET" if stamp else _TIME_NOT_RECORDED_TEXT


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    # An int is always finite; math.isfinite would convert a huge one to a float.
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


def _is_whole_number(value: object) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    return isinstance(value, float) and math.isfinite(value) and value.is_integer()


app.jinja_env.tests["finite_number"] = _is_finite_number
app.jinja_env.tests["whole_number"] = _is_whole_number


def _fetch_tenure_review() -> tuple[dict | None, list[dict], dict, bool]:
    """Return (run header, entries, run state, read_failed) for the newest stored tenure review.

    Shared by GET /api/retirement-recommendations and the AI Advisor panel. Reads
    only: the stored run row, the decisions table and the in-process run state.
    The header is the stored run record without its entries; entries are copies
    of the stored entries (non-dict items skipped) plus a live approval_status.
    read_failed is True when the newest run row cannot be used (the read raised,
    the record is not a dict or carries no run_id, or the review module cannot
    be imported); the header is then None and the entries are empty, and the
    callers list no recommendation, because which run owns them is unknown.
    No stored run at all is not a failure. A stored entries value that is not a list makes the run unreadable. The review_run_id that the caller
    passes to _fetch_retirement_recommendations is the header's run_id.
    Never raises.
    """
    status: dict = {
        "running": False,
        "running_since": None,
        "trigger": None,
        "last_failed_attempt": None,
    }
    try:
        from advisors import tenure_review  # noqa: PLC0415
    except Exception as exc:
        _daemon_log.debug(
            "_fetch_tenure_review: review module unavailable (%s)", type(exc).__name__
        )
        return None, [], status, True
    try:
        status = tenure_review.REVIEW_RUN_STATE.snapshot()
    except Exception as exc:
        _daemon_log.debug("_fetch_tenure_review: run state unavailable (%s)", type(exc).__name__)
    run: dict | None = None
    entries: list[dict] = []
    read_failed = False
    try:
        rows = database.get_advisor_observations_for_role(
            tenure_review.TENURE_REVIEW_RUN_ROLE, limit=1
        )
        if rows:
            raw = rows[0].get("raw_response")
            if isinstance(raw, dict) and raw.get("run_id"):
                stored = raw.get("entries")
                if isinstance(stored, list):
                    run = {k: v for k, v in raw.items() if k != "entries"}
                    entries = [dict(e) for e in stored if isinstance(e, dict)]
                else:
                    read_failed = True
            else:
                read_failed = True
    except Exception as exc:
        _daemon_log.debug("_fetch_tenure_review: read failed (%s)", type(exc).__name__)
        run, entries, read_failed = None, [], True
    _join_retirement_approval_status(entries, id_key="symphony_id", absent_status=None)
    return run, entries, status, read_failed


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
    level>, ...], "last_run": <dict | null>, "cards_run": <dict | null>,
    "review_run": <newest stored review run header or null>,
    "review_entries": [<one stored entry per live symphony, plus a live
    approval_status that is null when no decision exists>], "review_status":
    {running, running_since, trigger, last_failed_attempt}, "review_read_failed":
    <true when the newest stored review cannot be read, else false>} --
    exactly those seven keys.

    "last_run" (Cycle 2e, AC-5) is the latest RETIREMENT_RUN_SUMMARY row's
    raw_response of ANY status (run_id/run_at/source/status/per-gate blocked
    counts/excluded symphonies/... -- see advisors.retirement_recommender's
    summary), null when no run has been recorded or its read fails (the rest of
    the response is still served, status stays 200). "cards_run" (AC-5b) is the
    summary the recommender run's "recommendations" were scoped to -- the newest
    run with status "ok" and a run_id (== last_run when the latest run is ok; an
    earlier run's summary after a failed night) -- and null when there is no such
    run or its read fails. A failed night therefore never empties the
    recommendations; an ok run that found nothing does. The Run-now JS polls
    this route until last_run.run_id changes.

    The "review_*" keys describe the symphony tenure review: the stored run
    header (run_id, as_of_trading_day, started_at, completed_at, trigger,
    threshold_in_effect, threshold_note and composite_window), one entry per live
    symphony, and the in-process run state.

    The "recommendations" list is the union of both producers' latest runs (see
    _fetch_retirement_recommendations): with a completed review run it holds the
    rows carrying that run's review_run_id; with a successful recommender run it
    also holds the rows carrying that run's run_id; with neither it holds the
    latest night's rows; a review run row that cannot be read contributes no
    rows.

    Each recommendation item carries the authoritative raw_response schema
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

    Empty -> {"recommendations": []} with last_run and cards_run null,
    review_run null, review_entries [], review_status the idle run state and
    review_read_failed false, never a 500. A stored review run's header holds
    run_id, as_of_trading_day, started_at, completed_at, trigger,
    threshold_in_effect, threshold_note and composite_window. A read failure
    degrades to the same empty list rather than 500ing (mirrors api_incubation).

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
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return value

    # Read the latest summary ONCE and resolve the cards' run from that same
    # snapshot, so "last_run", "cards_run" and the run-scoped "recommendations"
    # can never disagree about which run is current (a run could finish between
    # two separate reads).
    try:
        last_run = _fetch_retirement_last_run()
    except Exception:
        _daemon_log.debug("api_retirement_recommendations: last_run read failed", exc_info=True)
        last_run = None

    # The run the cards are scoped to (AC-5b): the latest SUCCESSFUL run, which
    # is last_run itself when that is ok, else an earlier run's summary.
    try:
        cards_run = _fetch_retirement_cards_run(last_run)
    except Exception:
        _daemon_log.debug("api_retirement_recommendations: cards_run read failed", exc_info=True)
        cards_run = None

    review_run, review_entries, review_status, review_read_failed = _fetch_tenure_review()
    try:
        raw_rows = _fetch_retirement_recommendations(
            review_run_id=review_run.get("run_id") if review_run else None,
            review_unreadable=review_read_failed,
            cards_run=cards_run,
        )
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

    return jsonify(
        {
            "recommendations": out,
            "last_run": _json_safe(last_run),
            "cards_run": _json_safe(cards_run),
            "review_run": _json_safe(review_run),
            "review_entries": _json_safe(review_entries),
            "review_status": _json_safe(review_status),
            "review_read_failed": review_read_failed,
        }
    )


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
    not a 404. An unexpected lookup failure answers 500 with an error body
    ({"error", "symphony_id"}) and no zeroed windows.
    """
    import autotuner  # noqa: PLC0415 -- lazy per CC-2 precedent (app.py:3119), keeps Optuna deps off module load

    symphony_id = request.args.get("symphony_id", "").strip()
    if not symphony_id:
        return jsonify({"error": "symphony_id is required"}), 400

    try:
        # A closed market counts back from the close of the session its figures belong to, so the
        # counts hold still while nothing is trading; an open one counts back from now.
        session_anchor = _current_session()[0].cutoff_anchor
        if session_anchor is None:
            stats = database.get_exit_turnover_stats(symphony_id)
        else:
            stats = database.get_exit_turnover_stats(
                symphony_id, now_utc=_session_close_moment(session_anchor).astimezone(UTC)
            )
        drag_pct = database.compute_est_annual_friction_drag_pct(
            stats, autotuner.SIM_EXIT_FRICTION_PCT
        )
    except Exception as exc:
        _daemon_log.warning("exit_turnover: stats lookup failed (%s)", type(exc).__name__)
        # A failed read is stated, never answered as zero exits.
        return jsonify({"error": "exit turnover unavailable", "symphony_id": symphony_id}), 500

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
    _cut_conn = None
    try:
        chart_data = database.load_chart_history()
        symphony_data = chart_data.get("symphonies", {}).get(symphony_id, [])
        # Today's ET date, and the session the stored tape belongs to: the date the engine stamps
        # in the chart store at its day rollover. A store that names no date names no session (it
        # is never filled in from today's date). The held overlay is read for the tape's own
        # session, else for today's ET date; the host's local date plays no part, because the host
        # runs on UTC and its date is the next day from 20:00 ET.
        et_date = datetime.now(_ET).strftime("%Y-%m-%d")
        stored_date = chart_data.get("date")
        session_date = stored_date if isinstance(stored_date, str) and stored_date else None
        today_str = session_date or et_date
        # After the close the tape is cut at the close snapshot's capture moment, like every
        # other read of a closed market, so rows the engine writes later cannot move a sparkline.
        trading_session = _current_session()[0]
        rows_conn = _ro_conn
        if trading_session.snapshot is not None:
            _cut_conn = _committed_shadow_conn(trading_session.cut_state)
            rows_conn = _cut_conn
        try:
            rows = rows_conn.execute(
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

        body = {"status": "success", "data": symphony_data, "et_date": et_date}
        if session_date is not None:
            body["session_date"] = session_date
        return jsonify(body)
    except Exception as e:
        _daemon_log.error("get_chart_data failed for %s: %s", symphony_id, e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500
    finally:
        _ro_conn.close()
        if _cut_conn is not None:
            _cut_conn.close()


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


# How long the dismiss route waits for its write before it answers 503. The state database's
# connect timeout is 10 s (database.py), so a lock wait SQLite itself would survive is not reported as
# a failure: 10 s plus 2 s. Requests run on their own threads, so the wait never touches the engine
# process or the minute scheduler.
_FLEET_DISMISS_WRITE_WAIT_S: float = 12.0


@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    # Side-effect ban: write_fleet_alert must not run on the request thread. The write is
    # submitted to the module-level executor and the route waits for it for a bounded time,
    # so "ok" is only ever answered after the dismissed_at_et write has landed.
    def _dismiss_async():
        try:
            row = database.read_fleet_alert()
            # The first dismissal time is kept: a repeat click never re-stamps it.
            if row is not None and row.get("dismissed_at_et") is None:
                row["dismissed_at_et"] = datetime.now(_ET).isoformat()
                database.write_fleet_alert(row)
        except Exception:
            logging.error("fleet_alert_dismiss: background write failed", exc_info=True)
            raise

    future = _DISMISS_EXECUTOR.submit(_dismiss_async)
    try:
        future.result(timeout=_FLEET_DISMISS_WRITE_WAIT_S)
    except Exception as exc:
        # A write that raised, or that did not land within the wait: never "ok", and a fixed
        # message so no exception text reaches the page.
        future.cancel()
        _daemon_log.warning("fleet_alert_dismiss not saved: %s", type(exc).__name__)
        return jsonify({"status": "error", "message": "Dismissal not saved"}), 503
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
@app.route("/api/history/<any(ytd):days>")
def get_history(days):
    # `days` is a calendar-day count or the token "ytd" (1 January through today,
    # resolved by analytics._window_cutoff_date like every other window token).
    # The window counts back from the session the figures belong to, never the wall clock.
    session = _current_session()[0]
    stats = analytics.get_history_summary(
        days=days,
        base_dir=analytics._POST_MORTEMS_DIR,
        exits_day=session.wording_day or session.anchor,
        **session.anchor_kwargs,
    )
    # The exits are the anchor session's: today's while open, the closed session's all the way to
    # the next open (the anchor is the one window_anchor of /api/state names).
    stats["todays_exits_day"] = (session.wording_day or session.anchor).isoformat()
    stats["todays_exits_market_closed"] = session.wording_day is not None
    stats["window_days"] = days
    # AC-5 (DE-PERF-WINDOW-TRUTH-001): window_days keeps its EXISTING
    # "requested window" meaning (not redefined) -- these three fields are
    # ADDITIVE honesty markers mirroring database.get_exit_turnover_stats'
    # established `coverage_days = min(window, actual_days)` pattern
    # (app.py:4329-4332's rationale). daily_dates is the sorted list of days a
    # real post-mortem file was actually parsed for -- the honest count of
    # trading days actually backing this response, independent of how many
    # were requested. An empty base_dir (day-1 droplet) honestly reports 0,
    # never a fabricated non-zero count.
    _hist_daily_dates = stats.get("daily_dates") or []
    _hist_actual_days = len(_hist_daily_dates)
    stats["actual_days"] = _hist_actual_days
    stats["coverage_days"] = (
        _hist_actual_days if isinstance(days, str) else min(days, _hist_actual_days)
    )
    stats["date_range"] = {
        "start": _hist_daily_dates[0] if _hist_daily_dates else None,
        "end": _hist_daily_dates[-1] if _hist_daily_dates else None,
    }

    # AC-3 (Finding 3): todays_exits is empty every trading day until the 15:54 ET
    # post-mortem write — not just on a day-1 droplet.  Backfill from exit_triggers
    # filtered to the CURRENT ET trading day, shaped exactly as history.js consumes
    # (ts / symphony_name / reason / detail — the same shape the post-mortem path
    # emits).  A zero-exit day stays honestly empty, and the windowed trigger_count
    # is never overwritten by the feed length (total_saved/win_rate derive from the
    # same windowed post-mortem entries).
    _data_instants: list = []
    for _day_text, _day_exits in (stats.get("daily_exits") or {}).items():
        for _day_exit in _day_exits or []:
            _instant = _et_instant_of(_day_text, _day_exit.get("time_triggered"))
            if _instant is not None:
                _data_instants.append(_instant)
    if not stats.get("todays_exits"):
        try:
            _today_et = session.anchor.isoformat()
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
            for _row in _rows:
                _instant = _et_instant_of(None, _row[1])
                if _instant is not None:
                    _data_instants.append(_instant)
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

    # The date and time of the newest exit this response shows (a post-mortem entry or a backfilled
    # exit), in the dated form every page stamp uses; "--" when no exit carries a time.
    stats["data_as_of"] = _format_data_stamp(max(_data_instants)) if _data_instants else _NO_STAMP

    return jsonify(stats)


# --- 2b. History Tab ---
@app.route("/history")
def history_page():
    """Render the Guard Alpha History tab (read-only operator surface)."""
    # While the market is closed the exits are the closed session's, so the first paint names that
    # day (as static/history.js does once /api/history answers) instead of reading "Today's exits".
    session = _current_session()[0]
    exits_label = "Today's exits"
    if session.wording_day is not None:
        exits_label = f"Exits on {session.wording_day.strftime('%b')} {session.wording_day.day}"
    return render_template(
        "history.html",
        active_route="history",
        exits_label=exits_label,
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
        copy_values=_page_copy_values(("friction_pct", "min_history_days")),
        active_route="performance",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
    )


@app.route("/api/performance")
def api_performance():
    """Performance time series + quantstats metrics.

    Query params:
        scope:        "aggregate" (default) or "symphony"
        days:         an integer trailing-row count, or a calendar window token
                      (30d 60d 90d 125d ytd 1y 5y); default 60
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
    # `days` is either a window token (30d 60d 90d 125d ytd 1y 5y), resolved to a
    # CALENDAR cutoff by the same analytics._window_cutoff_date helper /api/hero-chart
    # and /api/strip use (AC-5 MAPERF-03), or an integer trailing-row count (the
    # dashboard detail panel sends days=60 and keeps getting its last 60 rows).
    is_window_token = raw_days in _PERFORMANCE_WINDOW_TOKENS
    if is_window_token:
        days: int | str = raw_days
    else:
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            return jsonify(
                {
                    "status": "error",
                    "message": (
                        "days must be an integer or one of " + ", ".join(_PERFORMANCE_WINDOW_TOKENS)
                    ),
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

    # A window token fetches the FULL series and slices it to the token's calendar
    # cutoff (both scopes); an integer fetches exactly `days` trailing trading days.
    _fetch_days = None if is_window_token else days
    # A closed market's reads are cut at the close snapshot's capture moment and its windows count
    # back from the session it closed, like every other route: rows the engine writes after the
    # close, and the wall clock, cannot move a number.
    now_et = datetime.now(_ET)
    trading_session = _current_session(now_et)[0]
    closed_conn = None
    closed_reads: dict = {}
    try:
        if trading_session.snapshot is not None:
            closed_conn = _committed_shadow_conn(trading_session.cut_state)
            closed_reads = {"conn": closed_conn}
            closed_weights = _frozen_symphony_inputs(trading_session.snapshot)[1]
            closed_weights = _without_symphonies(
                closed_weights, _no_longer_held_ids(closed_weights, closed_conn, now_et)
            )
            series_reads = {"conn": closed_conn, "state": closed_weights}
        else:
            series_reads = None

        if scope == "aggregate":
            # Finding 4: the aggregate series is the CANONICAL value-weighted portfolio
            # series from shadow_history — the same series /api/hero-chart compounds —
            # never the post-mortem trigger arrays (a selection-biased exit-snapshot
            # event sample that dropped zero-trigger days and contradicted the Overview
            # chart on the same screen).  Every shadow_history trading day appears.
            dates, live_returns, shadow_returns = [], [], []
            # The value weights leave out a symphony no longer held (the same rule every page asks).
            _held_weights = series_reads if series_reads is not None else _held_state_kwargs(now_et)
            try:
                _series = analytics.get_portfolio_bot_and_held_daily_returns(
                    days=_fetch_days, **_held_weights
                )
                if is_window_token:
                    _series = _slice_series_by_window_cutoff(
                        _series, days, trading_session.cutoff_anchor
                    )
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
                    symphony_id, days=_fetch_days, **closed_reads
                )
                if is_window_token:
                    _sym_series = _slice_series_by_window_cutoff(
                        _sym_series, days, trading_session.cutoff_anchor
                    )
                if _sym_series is not None:
                    dates, shadow_returns, live_returns = _sym_series
            except Exception:
                _daemon_log.debug(
                    "api_performance: per-symphony shadow series failed", exc_info=True
                )

        # AC-2/AC-4 (MA-7/MAPERF-02): the day-1-droplet fallbacks below are
        # AGGREGATE-ONLY. A scope=symphony request for a symphony with zero
        # shadow_history rows must render an honest empty state — never the whole
        # PORTFOLIO's non-empty series mislabeled under that symphony's name (both
        # fallbacks were previously unconditional).

        # AC-2: when the series is still empty (day-1 droplet), fall back to
        # shadow_history for the series so the chart is non-empty from day one.
        # The insufficient_history / quantstats-min-obs guard is unchanged.
        #
        # Both fallbacks read the UNWINDOWED shadow_history, so under a window token
        # each is sliced to that token's calendar cutoff (the same slice the primary
        # series gets): a row dated inside the window still charts, while rows older
        # than the window drop out and leave the honest empty state instead of being
        # returned under a window_start they predate.  An integer `days` is a row
        # count with no cutoff and keeps the fallbacks untouched.
        if not dates and scope == "aggregate":
            try:
                _fallback = analytics.get_portfolio_bot_and_held_daily_returns(**_held_weights)
                if is_window_token:
                    _fallback = _slice_series_by_window_cutoff(
                        _fallback, days, trading_session.cutoff_anchor
                    )
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
                _single = analytics.get_single_day_shadow_returns(**closed_reads)
                if is_window_token:
                    _single = _slice_series_by_window_cutoff(
                        _single, days, trading_session.cutoff_anchor
                    )
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

        # AC-5 (DE-PERF-WINDOW-TRUTH-001): window_days keeps its EXISTING
        # "requested window" meaning (not redefined) -- these three fields are
        # ADDITIVE honesty markers mirroring database.get_exit_turnover_stats'
        # established `coverage_days = min(window, actual_days)` pattern
        # (app.py:4329-4332's rationale). actual_days mirrors the already-computed
        # observation_count (both are len(dates)) -- kept as its own key rather
        # than aliasing so the AC-5 contract doesn't implicitly depend on
        # observation_count's name never changing. "ytd" has no fixed numeric
        # request to cap against, so coverage_days is the actual count in that case
        # (never a TypeError from min()'ing a string against an int). The same holds
        # for every window token: its span is calendar days, not a row count.
        _coverage_days = observation_count if is_window_token else min(days, observation_count)
        # window_start states the calendar cutoff a token window began at; an integer
        # window is a row count and has none.
        _window_start = None
        if is_window_token:
            _cutoff = _window_cutoff_of(trading_session, days)
            _window_start = _cutoff.isoformat() if isinstance(_cutoff, date) else None
        # The date and time of the newest shadow observation the response used: the last date's newest
        # stored row, across all symphonies (aggregate) or for the one symphony. "--" when the response
        # has no date or the date has no stored time.
        _performance_stamp = _NO_STAMP
        if len(dates):
            try:
                _latest_ts = database.get_latest_shadow_ts_utc(
                    str(dates[-1]), symphony_id if scope == "symphony" else None, **closed_reads
                )
                if _latest_ts:
                    _performance_stamp = _format_data_stamp(
                        _DatetimeType.fromisoformat(str(_latest_ts).replace("Z", "+00:00"))
                    )
            except Exception:
                _daemon_log.debug("api_performance: data_as_of unavailable", exc_info=True)
        response_body = {
            "scope": scope,
            "dates": list(dates),
            "data_as_of": _performance_stamp,
            "live_returns": live_returns_out,
            "shadow_returns": shadow_returns_out,
            "live_metrics": live_metrics,
            "shadow_metrics": shadow_metrics,
            "observation_count": observation_count,
            "insufficient_history": insufficient_history,
            "window_days": days,
            "window_start": _window_start,
            "actual_days": observation_count,
            "coverage_days": _coverage_days,
            "date_range": {
                "start": dates[0] if len(dates) else None,
                "end": dates[-1] if len(dates) else None,
            },
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
    finally:
        if closed_conn is not None:
            closed_conn.close()


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
    # A symphony no longer held is not offered: the picker and the Advisor's select are filled
    # from here, and neither can read a series for it that means anything.
    state = _without_symphonies(state, _no_longer_held_ids(state, None, datetime.now(_ET)))
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
    live_mode = env_flags.is_true(env_vars.get("LIVE_EXECUTION", "False"))

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

    sleeve_live_execution = env_flags.is_true(
        env_vars.get("SLEEVE_LIVE_EXECUTION", "False") or "False"
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
    # A symphony no longer held is listed apart, so Settings can show it dimmed: it is known,
    # but it has no override to edit and is not part of the active roster.
    no_longer_held_ids = _no_longer_held_ids(state_data, None, datetime.now(_ET))
    symphony_names = set()
    raw_names = {}  # normalized_name -> display name
    gone_names: dict[str, str] = {}  # normalized_name -> display name, no longer held
    for sym_id, data in state_data.items():
        if isinstance(data, dict) and "name" in data:
            norm = database.normalize_name(data["name"])
            if sym_id in no_longer_held_ids:
                gone_names[norm] = data["name"]
                continue
            symphony_names.add(norm)
            raw_names[norm] = data["name"]
    # The same normalized name held under another id keeps the entry active.
    gone_names = {norm: name for norm, name in gone_names.items() if norm not in symphony_names}

    symphonies_data = {}
    for name in symphony_names:
        symphonies_data[name] = database.get_symphony_strategy(name, create_if_missing=False)

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
            "symphonies_no_longer_held": [
                {"id": norm, "name": gone_names[norm]} for norm in sorted(gone_names)
            ],
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


# EXECUTION_START_TIME is accepted as "H:MM" or "HH:MM": hours 0-23, minutes exactly two digits
# 00-59.  The engine splits the value on ":" and converts both halves to int
# (alpha_bot_execution.py), so a one-digit hour reads correctly there, and the production .env
# already holds '9:35'; refusing that form would make the operator retype a value the engine
# accepts.  A one-digit minute, 24:00, 9:60, an empty value and any surrounding text stay refused.
# fullmatch, never match with "$": "$" also matches before a trailing newline, which is how a
# second line reaches the file.
_SETTINGS_START_TIME = re.compile(r"(?:[01]?[0-9]|2[0-3]):[0-5][0-9]")
# The two values engine/exit_authority.py recognizes; anything else degrades the badge.
_SETTINGS_EXIT_AUTHORITIES = frozenset({"per_symphony", "port_level"})


def _settings_number(value) -> float | None:
    """The finite float a settings value stands for, or None when it is not a plain finite number.

    A string must be ASCII with nothing around the number: float() itself strips whitespace, and
    a newline that float() forgives would still be written to the file as a second line.  bool is
    refused (float(True) is 1.0), as are nan, inf and anything that overflows to inf.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.isascii() or value != value.strip():
            return None
    elif not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _settings_global_value_is_valid(key: str, value) -> bool:
    """Whether `value` may be written to .env for the allowlisted global `key`.

    python-dotenv writes KEY='value' and escapes only the quote character, so a value ending in a
    backslash lets the next quote in the file close it and a following value can carry a whole
    line of its own (LIVE_EXECUTION=True, which the key allowlist keeps out).  Each key therefore
    takes only its own plain shape.
    """
    if key == "EXECUTION_START_TIME":
        return isinstance(value, str) and _SETTINGS_START_TIME.fullmatch(value) is not None
    if key == "EXIT_AUTHORITY":
        return isinstance(value, str) and value in _SETTINGS_EXIT_AUTHORITIES
    return _settings_number(value) is not None


@app.route("/api/settings", methods=["POST"])
def save_settings():
    """Saves allowlisted globals to .env and symphony strategies to SQLite.

    Rejects any key not in _SETTINGS_WRITE_ALLOWLIST (including LIVE_EXECUTION
    and all credential keys) with a 400 so the client gets an actionable error
    rather than a silent no-op.  Every value is validated before the first write
    (all or nothing): a bad value anywhere in the payload is a 400 that leaves
    .env and the strategies as they were.
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

        # Validate everything first.  The message names allowlisted keys only, never a value.
        bad_globals = sorted(
            key
            for key, val in globals_payload.items()
            if not _settings_global_value_is_valid(key, val)
        )
        strategies: list[tuple[str, dict[str, float], list]] = []
        symphony_param_is_bad = False
        for sym_name, strategy_data in payload.get("symphonies", {}).items():
            params: dict[str, float] = {}
            for param_key, param_val in strategy_data.get("params", {}).items():
                number = _settings_number(param_val)
                if number is None:
                    symphony_param_is_bad = True
                else:
                    params[param_key] = number
            strategies.append((sym_name, params, strategy_data.get("locked_vars", [])))
        if bad_globals or symphony_param_is_bad:
            problems = []
            if bad_globals:
                problems.append(f"invalid values for settings {bad_globals}")
            if symphony_param_is_bad:
                problems.append("a symphony parameter is not a finite number")
            return jsonify(
                {"status": "error", "message": "Nothing was saved: " + "; ".join(problems)}
            ), 400

        for key, val in globals_payload.items():
            set_key(ENV_FILE_PATH, key, str(val))

        # AC-P2.2.4: record timestamp when EXIT_AUTHORITY is changed so the sticky
        # restart notice can compare against daemon_started_at (panel BC H7).
        if "EXIT_AUTHORITY" in globals_payload:
            _changed_at = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            set_key(ENV_FILE_PATH, "_exit_authority_changed_at", _changed_at)

        # Save Symphony Strategies
        for sym_name, params, locked in strategies:
            database.save_symphony_strategy(sym_name, params, locked)

        return jsonify({"status": "success", "message": "Variables updated successfully!"})
    except Exception as e:
        _daemon_log.error("save_settings failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


@app.route("/api/symphony-settings/<symphony_name>", methods=["GET"])
def get_symphony_settings(symphony_name: str):
    """Return per-symphony modal state: live_mode, global_live, parameters, locked_vars, advisor observations, display_name.  # noqa: E501  # un-wrappable long line

    Consumes the read-only DB path for parameters/locked_vars/live_mode and
    the env for the global master-switch flag.  Never writes; never reruns the engine.
    """
    symphony_name = database.normalize_name(symphony_name)
    # Use module-level dotenv_values so test patches on app_module.dotenv_values take effect
    # (same convention as save_settings and other dashboard routes).
    env_vars = dotenv_values(ENV_FILE_PATH)
    global_live = env_flags.is_true(env_vars.get("LIVE_EXECUTION", "False"))

    strategy = database.get_symphony_strategy(symphony_name, create_if_missing=False)
    # get_symphony_strategy defaults live_mode=False (arch rule 4: explicit, never by omission).
    live_mode = bool(strategy.get("live_mode", False))

    # advisor_observations.symphony_id is keyed by normalize_name(sym["name"])
    # (written by autotuner.py:save_autotune_run(symphony_id=normalized_name)).
    # The URL param (symphony_name) arrives as a Composer hash like 'iaSOOUsmnCJHiZvbrWfs'
    # normalized to its lowercase form — never the human name.  Resolve hash → name
    # via bot_state before querying, falling back to symphony_name if no match found.
    bot_state = database.load_state()
    obs_symphony_id = symphony_name  # fallback: pass as-is (may yield 0 rows on hash)
    # The live symphony's name; stays None when the id matches no bot_state entry.
    display_name = None
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
            display_name = sym_data["name"]
            break
    advisor_observations = database.get_advisor_observations_for_symphony(obs_symphony_id)

    return jsonify(
        {
            "live_mode": live_mode,
            "global_live": global_live,
            "parameters": strategy.get("params", {}),
            "locked_vars": strategy.get("locked_vars", []),
            "advisor_observations": advisor_observations,
            "display_name": display_name,
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

# The production go-live day (the first day the live engine ran against real accounts).  The engine
# writes a post-mortem every trading day from here on, so a file dated on or after this day is
# production history and the flush never deletes it, however recent: the set above names only the
# real files that existed when the synthetic backfill was cleaned up.  ISO dates compare as text.
_PRODUCTION_GO_LIVE_DAY = "2026-06-22"


# The arming flags the flush confirm lists, in the order shown: bot_state field -> label.
_ARMED_SYMPHONY_FLAGS = (
    ("armed", "ARMED"),
    ("tp_armed", "TP"),
    ("para_armed", "PARA"),
    ("triggered", "TRIGGERED"),
)


@app.route("/api/armed-symphonies", methods=["GET"])
def armed_symphonies():
    """The live symphonies that carry an arming flag: what the settings flush would reset.

    A small read (a few hundred bytes) for the flush confirm, in place of the whole ``/api/state``
    body. Sourced from the LIVE ``bot_state`` (never the frozen close snapshot) and filtered by the
    structural symphony-entry check, so the metadata keys are never listed. Every entry is labelled
    ``live``. Read-only; behind the auth gate.
    """
    try:
        armed = []
        for key, value in (database.load_state(read_only=True) or {}).items():
            if not _is_symphony_state_entry(value):
                continue
            flags = [label for field, label in _ARMED_SYMPHONY_FLAGS if value.get(field)]
            if flags:
                armed.append({"name": value.get("name") or key, "flags": flags, "state": "live"})
        return jsonify({"armed": armed})
    except Exception as exc:
        _daemon_log.error("armed symphonies read failed: %s", type(exc).__name__)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500


@app.route("/api/settings/flush-resync", methods=["POST"])
def flush_resync():
    """Delete synthetic post_mortem backfill files, reset per-symphony bot_state, resync Composer.

    Phase 1 — File purge: scans analytics._POST_MORTEMS_DIR, removes files whose dates are
      NOT in _REAL_POST_MORTEM_DATES and fall before _PRODUCTION_GO_LIVE_DAY, keeps real ones
      and every file from the go-live day on.  Invalidates the analytics cache.
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
        if date_str in _REAL_POST_MORTEM_DATES or date_str >= _PRODUCTION_GO_LIVE_DAY:
            kept.append(fname)
        else:
            try:
                os.remove(fpath)
                deleted.append(fname)
            except OSError as exc:
                # The answer carries the exception TYPE only (never its text, which can hold a
                # path); the log line keeps the detail.
                errors.append(f"{fname}: {type(exc).__name__}")
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
        errors.append(f"state_read: {type(exc).__name__}")

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
        _daemon_log.error("flush_resync: Composer resync failed: %s", type(exc).__name__)
        _daemon_log.debug("flush_resync: Composer resync detail: %s", exc, exc_info=True)
        errors.append(f"resync: {type(exc).__name__}")

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


# Strategy Builder card labels (UX data-truth AC-7): the ONE mapping from the
# stored objective / template values to the words a card shows. The Jinja
# stored cards read labels stamped from it in ai_advisor_tab(); the live-run
# renderer (static/ai_advisor.js) reads the same mapping from the Strategy
# Builder panel's data-sb-labels attribute, so the two surfaces cannot drift.
_SB_OBJECTIVE_LABELS = {
    "diversify": "Diversify portfolio",
    "cut_drawdown": "Cut drawdown",
    "lift_risk_adjusted": "Lift risk-adjusted return",
    "volatility_mitigation": "Volatility mitigation",
}
_SB_SOURCE_LABELS = {
    "built-new": "Built new",
    "atlas-suggested": "Atlas community",
}
# Shown when a stored value is absent, null, or not a key of its mapping --
# never the raw stored value, an underscore token, or a blank.
_SB_LABEL_NOT_RECORDED = "not recorded"


def _sb_label(mapping: "dict[str, str]", stored_value) -> "str | None":
    """Return the human label for a stored value, or None when it has none."""
    if isinstance(stored_value, str):
        return mapping.get(stored_value)
    return None


def _symphony_display_names_by_key(bot_state) -> "dict[str, str]":
    """Map each live symphony's normalized name to its display name as stored.

    Advisor rows (autotune runs, observations) are keyed by
    ``database.normalize_name(display_name)``, not by Composer hash, so the
    lookup key is the normalized name. Only entries shaped like a symphony
    (a dict with a string ``name``) count: ``bot_state`` also holds portfolio
    metadata that has no name. The first entry wins when two normalize alike.
    """
    names: dict[str, str] = {}
    if not isinstance(bot_state, dict):
        return names
    for entry in bot_state.values():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str):
            names.setdefault(database.normalize_name(name), name)
    return names


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


_VERDICT_BADGE_CLASS_UNVERIFIED = "prism-verify-badge--unverifiable"
_VERIFIED_COUNT_UNAVAILABLE_LABEL = "verified count unavailable"


def _readable_verified_counts(summary: "dict | None") -> "tuple[int, int] | None":
    """Return (n_pass, n_checks) when the summary holds a usable pair, else None.

    Usable means both are real integers (a bool is not one), the check count is
    positive and the passing count lies between zero and the check count. A
    missing, null, string or out-of-range value means the pill cannot state a
    count.
    """
    if not isinstance(summary, dict):
        return None
    n_pass = summary.get("n_pass")
    n_checks = summary.get("n_checks")
    for value in (n_pass, n_checks):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    if n_checks < 1 or not 0 <= n_pass <= n_checks:
        return None
    return n_pass, n_checks


def _build_verdict_display(
    verdict: "str | None", summary: "dict | None" = None
) -> "tuple[str, str]":
    """Map a MARKET_PRISM_VERIFICATION verdict string to (label, css_class) for
    the Overview tab's verdict pill (2026-07 redesign, /review PR #92).

    A recognized verdict (clean/flags-detected/overrides-detected/
    no-verifiable-claims/no-numeric-claims) renders with its label verbatim and
    the matching check-badge class. An unrecognized or missing verdict must
    NOT silently render as "no-numeric-claims" (that would misleadingly assert
    "nothing was checked" when the data doesn't say so) — it renders a neutral
    unverifiable-class pill labeled with the raw string, or "unknown" when
    verdict itself is falsy.

    A `clean` verdict reads "clean" in the pass style only when every check
    passed. When some did not, the pill states the count ("18 of 32 verified")
    in the unverifiable style, because a green "clean" over unverifiable checks
    overstates what was verified; when the summary holds no usable counts it
    reads "verified count unavailable" in the same style. Every other verdict
    ignores `summary`.
    """
    if verdict == "clean":
        counts = _readable_verified_counts(summary)
        if counts is None:
            return _VERIFIED_COUNT_UNAVAILABLE_LABEL, _VERDICT_BADGE_CLASS_UNVERIFIED
        n_pass, n_checks = counts
        if n_pass < n_checks:
            return f"{n_pass} of {n_checks} verified", _VERDICT_BADGE_CLASS_UNVERIFIED
    label = verdict if verdict else "unknown"
    css_class = _VERDICT_BADGE_CLASS.get(verdict, _VERDICT_BADGE_CLASS_UNVERIFIED)
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
      observation_groups — per-producer status lines for the Overview panel, each
                           {role, total, rows}: the newest kept rows (at most 50,
                           newest first) and the producer's whole kept-row count
      observations_unavailable — True when the observation read failed
      correlation_matrix_view — the Correlations panel, read from the stored retirement run
      crisis_caveat      — instability warning string
      no_api_key         — True when Composer credentials absent (swaps + logic)
      symphonies         — known symphony IDs (swap + logic forms)
      chat_available     — True when ANTHROPIC_API_KEY is set
    """
    # ------------------------------------------------------------------ #
    # Overview panel: advisor observations                                  #
    # ------------------------------------------------------------------ #
    # One status line per producer with its history behind a closed expander. A
    # failed read shows the unavailable text in place of the lines; it never takes
    # the rest of the page down and never reads as an empty history.
    observation_groups: list[dict] = []
    observations_unavailable = False
    try:
        observation_groups = _build_advisor_observation_groups()
    except Exception as exc:
        observation_groups = []
        observations_unavailable = True
        logging.getLogger(__name__).warning(
            "advisor observations unavailable: %s", type(exc).__name__
        )
    # Every listed row, flat, for the display-name stamp below.
    observations: list[dict] = [row for group in observation_groups for row in group["rows"]]

    # ------------------------------------------------------------------ #
    # Correlations panel: the instability caveat is always on, so it is    #
    # read in its own try, independent of every run read below. The matrix #
    # itself is built from the stored retirement run further down.         #
    # Lazy import keeps the module off the live 1-minute execution path.   #
    # ------------------------------------------------------------------ #
    crisis_caveat: str = ""
    try:
        from advisors import correlation_diagnostic as _corr_diag  # noqa: PLC0415

        crisis_caveat = _corr_diag.CRISIS_CAVEAT
    except Exception:
        pass

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
                    _verdict_label, _verdict_class = _build_verdict_display(
                        _ver_verdict, _ver_summary
                    )
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
            # Card words (AC-7): None when the stored value has no label; the
            # template then reads the shared "not recorded" text.
            _obs["_sb_objective_label"] = _sb_label(_SB_OBJECTIVE_LABELS, _rr.get("objective"))
            _obs["_sb_source_label"] = _sb_label(_SB_SOURCE_LABELS, _rr.get("template_id"))
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

    # Observations table, Symphony cell (UX data-truth AC-5): stamp each row's
    # live display name (`_symphony_name`) so the cell reads the name and keeps
    # the stored key only in a title attribute. A row whose key matches no live
    # symphony, or an empty key, is left unstamped and the cell reads the stored
    # key (or the dash). Reads the shared lazy bot_state, and only when some row
    # carries a key to look up.
    if any(isinstance(_o.get("symphony_id"), str) and _o["symphony_id"] for _o in observations):
        _obs_names = _symphony_display_names_by_key(_ensure_ai_advisor_bot_state())
        for _o in observations:
            _o_key = _o.get("symphony_id")
            if isinstance(_o_key, str) and _o_key:
                _o_name = _obs_names.get(database.normalize_name(_o_key))
                if _o_name:
                    _o["_symphony_name"] = _o_name

    # Cycle 2e (retirement-visibility AC-5/AC-6): the "last run" block. Read
    # ONCE here (the latest run of ANY status) and the cards' run (AC-5b: the
    # latest SUCCESSFUL run, which is the same dict when the latest run is ok)
    # is resolved from that snapshot, then handed to
    # _fetch_retirement_recommendations below, so the block, the earlier-run
    # note and the run-scoped cards always describe consistent runs. A read
    # failure degrades to None -> the template's honest "Not run yet" block
    # (never a 500); a failed cards-run lookup falls back to the legacy date
    # filter.
    retirement_last_run: dict | None = None
    retirement_runs_read_failed = False
    try:
        retirement_last_run = _fetch_retirement_last_run()
    except Exception:
        retirement_last_run = None
        retirement_runs_read_failed = True
    retirement_cards_run: dict | None = None
    try:
        retirement_cards_run = _fetch_retirement_cards_run(retirement_last_run)
    except Exception:
        retirement_cards_run = None
        retirement_runs_read_failed = True

    # Symphony tenure review: the newest stored review run, its entries, the
    # in-process run state and whether the stored run could be read.
    (
        tenure_review_run,
        tenure_review_entries,
        tenure_review_status,
        tenure_review_read_failed,
    ) = _fetch_tenure_review()
    tenure_review_completed_et = _format_eastern_run_time(
        tenure_review_run.get("completed_at") if tenure_review_run else None
    )

    retirement_recommendations: list[dict] = []
    # The cards below the review are the recommender run's (Cycle 2e), or the
    # pre-run legacy cards when neither producer has a run. The review run's own
    # rows are rendered as review entries, so include_review_rows=False leaves
    # them out (no card twice), and a known review run (readable or not) turns
    # the legacy fallback off.
    try:
        # Cycle 2d (AC-4, PR#140 2nd /code-review finding 5): pass the
        # shared closure so _refresh_retirement_display_names's bot_state
        # need (inside _fetch_retirement_recommendations) is absorbed into
        # the SAME memoized load the checklist/frontrunner blocks below
        # use, instead of independently calling database.load_state() again.
        retirement_recommendations = _fetch_retirement_recommendations(
            bot_state_getter=_ensure_ai_advisor_bot_state,
            review_run_id=tenure_review_run.get("run_id") if tenure_review_run else None,
            review_unreadable=tenure_review_read_failed,
            cards_run=retirement_cards_run,
            include_review_rows=False,
        )
    except Exception:
        pass  # Empty-state rendered by template on [].

    # Excluded-symphony display names for the "last run" block: resolved at
    # request time against current bot_state through the SAME memoized closure
    # (zero extra load_state() when the run excluded nothing).
    retirement_last_run_excluded = _retirement_resolve_excluded_symphonies(
        retirement_last_run, _ensure_ai_advisor_bot_state
    )

    # The Correlations panel and the Retirement tab's "Series through" line read
    # the same stored run: the matrix from the newest successful run, the date
    # from the latest run. A failed read of either run shows the unavailable state
    # (it must never look like a run that never happened). No series is fetched.
    retirement_series_through = _retirement_series_through(retirement_last_run)
    correlation_matrix_view = _retirement_correlation_matrix_view(
        retirement_last_run,
        retirement_cards_run,
        read_failed=retirement_runs_read_failed,
        bot_state_getter=_ensure_ai_advisor_bot_state,
    )

    # ------------------------------------------------------------------ #
    # Retirement approval status + display names: both now resolved       #
    # INSIDE _fetch_retirement_recommendations() itself -- approval_status #
    # via _join_retirement_approval_status (Cycle 2c) and                  #
    # candidate_name/sibling_name via _refresh_retirement_display_names    #
    # (PR#140 review finding 3) -- so this panel and GET /api/retirement-  #
    # recommendations always agree on both. Every rec already carries a   #
    # fresh "approval_status" and fresh "candidate_name"/"sibling_name"   #
    # by the time it reaches here; the template reads those fields        #
    # directly (no separate panel-local resolution needed).               #
    # ------------------------------------------------------------------ #
    # Wind-down checklist: deterministic, no LLM, built ONLY
    # for approved items the panel renders -- never inside the approve route
    # itself (approving writes the status only). One loop serves the review
    # entries (their own symphony_id, named as their row is) and the
    # recommendation cards the panel lists (the recommender run's cards, or the
    # legacy cards when neither producer has a run). A completed review run's
    # own recommendation rows are not rendered as cards (the review entries
    # carry them), so they get no checklist of their own.
    _checklist_jobs: list[tuple[dict, dict, str | None]] = [
        (_entry, {"candidate_id": _entry.get("symphony_id")}, _entry.get("display_name"))
        for _entry in tenure_review_entries
        if _entry.get("approval_status") == "approved"
    ]
    _checklist_jobs += [
        (_rec, _rec, None)
        for _rec in retirement_recommendations
        if _rec.get("approval_status") == "approved"
    ]
    if _checklist_jobs:
        _ret_bot_state = _ensure_ai_advisor_bot_state()
        try:
            from advisors.retirement_checklist import build_checklist  # noqa: PLC0415
        except Exception:
            build_checklist = None
        for _target, _source, _row_name in _checklist_jobs:
            if build_checklist is None:
                _target["_checklist"] = None
                continue
            try:
                _checklist = build_checklist(_source, _ret_bot_state)
                if isinstance(_row_name, str) and _row_name and isinstance(_checklist, dict):
                    _checklist = {**_checklist, "candidate_name": _row_name}
                _target["_checklist"] = _checklist
            except Exception:
                _target["_checklist"] = None

    # The Retirement tab's stat tiles (an "ok" run only) and the Overview's one-line
    # pointer. Both are built from the run, cards and review entries read above, so the
    # page makes no read of its own for them.
    retirement_summary_tiles = _build_retirement_summary_tiles(retirement_last_run)
    retirement_pointer = _build_retirement_pointer(
        retirement_last_run, retirement_recommendations, tenure_review_entries
    )

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

    # The swap and logic forms list the symphonies the post-mortems name. One that is no longer
    # held (the shared rule) is not offered. The state is read only when there is a list to trim,
    # so a page with no history still costs no state read.
    if symphonies:
        try:
            _roster_state = _ensure_ai_advisor_bot_state()
            _roster_gone = _no_longer_held_ids(_roster_state, None, datetime.now(_ET))
            if _roster_gone:
                _gone_names = {
                    database.normalize_name(entry["name"])
                    for key, entry in _roster_state.items()
                    if key in _roster_gone and isinstance(entry, dict) and "name" in entry
                }
                _held_names = {
                    database.normalize_name(entry["name"])
                    for key, entry in _roster_state.items()
                    if key not in _roster_gone and _is_symphony_state_entry(entry)
                }
                _offered_not = _gone_names - _held_names
                symphonies = [
                    n for n in symphonies if database.normalize_name(n) not in _offered_not
                ]
        except Exception:
            pass  # The list then stays as the post-mortems named it.

    # Badges read the cached resolution only; the render never lists models,
    # so it can never wait on the Models API (the startup warm-up fills it).
    _suggestion_choice = model_config.peek(model_config.Knob.SUGGESTION)
    _synthesis_choice = model_config.peek(model_config.Knob.SYNTHESIS)
    advisor_suggestion_model = _suggestion_choice.badge_text
    advisor_synthesis_model = _synthesis_choice.badge_text
    # A model name is known when it resolved or an operator override names it.
    advisor_suggestion_model_known = _suggestion_choice.source != "unavailable"
    advisor_synthesis_model_known = _synthesis_choice.source != "unavailable"
    page_copy = _page_copy_values(
        ("tenure_threshold", "screen_threshold", "stress_fraction_pct", "watch_trail_pp"),
        tenure_review=tenure_review_run,
        retirement_last_run=retirement_last_run,
    )

    return render_template(
        "ai_advisor.html",
        active_route="advisor",
        meta=_build_meta({}, market_state=get_market_state(datetime.now(_ET))),
        observation_groups=observation_groups,
        observations_unavailable=observations_unavailable,
        crisis_caveat=crisis_caveat,
        correlation_matrix_view=correlation_matrix_view,
        retirement_series_through=retirement_series_through,
        no_api_key=no_api_key,
        symphonies=symphonies,
        chat_available=chat_available,
        sb_observations=sb_observations,
        sb_card_artifacts=sb_card_artifacts,
        sb_objective_labels=_SB_OBJECTIVE_LABELS,
        sb_source_labels=_SB_SOURCE_LABELS,
        sb_label_not_recorded=_SB_LABEL_NOT_RECORDED,
        market_prism_summary=market_prism_summary,
        market_prism_verification=market_prism_verification,
        retirement_recommendations=retirement_recommendations,
        retirement_last_run=retirement_last_run,
        retirement_cards_run=retirement_cards_run,
        retirement_last_run_excluded=retirement_last_run_excluded,
        retirement_summary_tiles=retirement_summary_tiles,
        retirement_pointer=retirement_pointer,
        tenure_review=tenure_review_run,
        tenure_review_entries=tenure_review_entries,
        tenure_review_status=tenure_review_status,
        tenure_review_read_failed=tenure_review_read_failed,
        tenure_review_completed_et=tenure_review_completed_et,
        frontrunner_proposals=frontrunner_proposals,
        overlay_not_recorded_text=overlay_not_recorded_text,
        advisor_suggestion_model=advisor_suggestion_model,
        advisor_synthesis_model=advisor_synthesis_model,
        advisor_suggestion_model_known=advisor_suggestion_model_known,
        advisor_synthesis_model_known=advisor_synthesis_model_known,
        page_copy=page_copy,
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
        "generation_model": llm_request.default_generation_model(),
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
        "generation_model": llm_request.default_generation_model(),
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
    mode_notice = None
    if built_new_count == 0:
        import model_config as _model_config  # noqa: PLC0415

        _gen_reason = getattr(run, "generation_reason", None)
        _reason_part = f" ({_gen_reason})" if isinstance(_gen_reason, str) and _gen_reason else ""
        _run_prov = getattr(run, "provenance", None)
        _choice = _model_config.peek(_model_config.Knob.SUGGESTION)
        if (isinstance(_run_prov, dict) and _run_prov.get("generation_model") == "") or (
            _choice.source == "unavailable"
        ):
            # No model served a reply (or none is known): the notice names none.
            _generation = "Built-new generation"
        else:
            _generation = f"Built-new generation ({_choice.badge_text})"
        mode_notice = (
            f"{_generation} produced 0 plans{_reason_part} (degraded): "
            f"this run surfaces {atlas_count} Atlas community candidate(s) only."
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

    run_id = str(uuid.uuid4())

    def _run_frontrunner_build_background(*, symphony_ids=None):
        summary = _frontrunner_build_failed_summary(run_id, "unknown")
        try:
            outcome = run_frontrunner_build(symphony_ids=symphony_ids)
            summary = _frontrunner_build_summary(run_id, outcome)
        except Exception as exc:
            # Log-and-swallow (mirrors _dismiss_async, app.py:2831): this is
            # a defense-in-depth net for a D-1 contract violation, not a
            # normal path — server-side log only, never surfaced to a
            # response (the 202 was already sent before this can fire).
            # The summary row names the exception's TYPE only, never its text.
            summary = _frontrunner_build_failed_summary(run_id, type(exc).__name__)
            _daemon_log.error(
                "ai_advisor_frontrunner_builder_run: background run failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
        finally:
            # Exactly one append-only row per on-demand run, written after the build ended,
            # so the page that polls for it learns the build is over (the weekly scheduler's
            # call does not come through here and writes none).
            _persist_frontrunner_build_summary(summary)

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

    return jsonify({"status": "started", "run_id": run_id}), 202


# The advisor_observations role that records how an on-demand Frontrunner build ended. Deliberately
# NOT in _ADVISOR_ROLES (like RETIREMENT_RUN_SUMMARY): it is a run record, not an advisor opinion.
_FRONTRUNNER_BUILD_SUMMARY_ROLE = "FRONTRUNNER_BUILD_SUMMARY"


def _frontrunner_build_failed_summary(run_id: str, error_code: str) -> dict:
    """The summary row body of a build that failed, with a fixed code (never exception text)."""
    return {
        "run_id": run_id,
        "status": "failed",
        "proposals": 0,
        "symphonies": 0,
        "failed_symphonies": 0,
        "error_code": error_code,
    }


def _frontrunner_build_summary(run_id: str, outcome: object) -> dict:
    """The summary row body for a build that returned, from ``run_frontrunner_build``'s outcome.

    ``roster_unavailable`` when the live roster could not be read, ``all_symphonies_failed`` when
    every symphony's build raised, otherwise ``completed`` with the number of proposal rows inserted
    (0 included: a build that found nothing worth queueing is a completed build).
    """
    if not isinstance(outcome, dict):
        return _frontrunner_build_failed_summary(run_id, "outcome_unavailable")
    symphonies = int(outcome.get("symphonies") or 0)
    failed = int(outcome.get("failed") or 0)
    summary = {
        "run_id": run_id,
        "status": "completed",
        "proposals": int(outcome.get("queued") or 0),
        "symphonies": symphonies,
        "failed_symphonies": failed,
        "error_code": None,
    }
    if outcome.get("roster_error"):
        summary.update(status="failed", error_code="roster_unavailable")
    elif symphonies > 0 and failed >= symphonies:
        summary.update(status="failed", error_code="all_symphonies_failed")
    return summary


def _persist_frontrunner_build_summary(summary: dict) -> None:
    """Append ONE FRONTRUNNER_BUILD_SUMMARY row. Never raises (D-1: type name logged only)."""
    try:
        database.insert_advisor_observation(
            advisor_role=_FRONTRUNNER_BUILD_SUMMARY_ROLE,
            subject_type="fleet",
            subject_id="frontrunner_build",
            verdict=summary.get("status"),
            raw_response=summary,
        )
    except Exception as exc:
        _daemon_log.error("Frontrunner build summary persist failed: %s", type(exc).__name__)


@app.route("/api/frontrunner-build/last-run", methods=["GET"])
def frontrunner_build_last_run():
    """The newest on-demand Frontrunner build's summary (``raw_response``), or ``{"last_run": null}``.

    Read-only (a read-only connection through the role accessor) and behind the auth gate; the page
    polls it after starting a build and shows the build's end when the run id it was given appears.
    """
    try:
        rows = database.get_advisor_observations_for_role(_FRONTRUNNER_BUILD_SUMMARY_ROLE, limit=1)
    except Exception as exc:
        _daemon_log.error("Frontrunner build last-run read failed: %s", type(exc).__name__)
        return jsonify({"status": "error", "message": "An internal error occurred"}), 500
    raw = rows[0].get("raw_response") if rows else None
    return jsonify({"last_run": raw if isinstance(raw, dict) else None})


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
    if not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 300:
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


@app.route("/ai-advisor/retirement/run", methods=["POST"])
def ai_advisor_retirement_run():
    """Operator-initiated "Run now" for the Retirement Recommender (Cycle 2e,
    AC-7): run the SAME worker as the 03:45 tick on demand.

    Never runs the worker inline. The worker reads every live symphony's daily
    series from Composer (rate-limited to 1 req/s) and may call the explainer
    LLM, so it is genuinely multi-minute and must never block a Flask request
    thread (dashboard Prime Directive: never blocks/slows the minute-by-minute
    execution loop). The route hands off to a daemon thread and answers 202
    immediately; the page learns the outcome by polling GET
    /api/retirement-recommendations until last_run.run_id changes.

    Market-hours gate (AC-7b): Composer's rate limit is per API key and the
    live engine bursts Composer calls at :00 every market minute, so while
    get_market_state(<now ET>) == "open" this answers 409
    {"status": "market_open"} BEFORE touching the single-flight lock -- no lock
    is taken, no thread is started, no worker runs. pre_market and
    closed_frozen proceed as below. The gate is checked first, so an open
    market reports market_open even while a run holds the lock. Only this
    route is gated: the scheduled 03:45 tick is always outside the session
    and stays ungated.

    Single-flight: _RETIREMENT_RUN_LOCK is shared with the scheduled tick and
    acquired NON-blocking in the request thread -- a run already in flight
    answers 409 {"status": "already_running"} and starts nothing; otherwise the
    daemon thread runs the worker and releases the lock in a finally (also
    released here when the thread fails to start).

    No request body is read. CSRF + auth come from the global
    _csrf_before_request / _auth_before_request hooks. NOT in
    _SETTINGS_WRITE_ALLOWLIST. Advisory-only: reaches no Composer
    create/trade primitive and never touches the live-execution flag -- the
    worker persists advisory rows and the run summary, nothing else. D-1: an
    error response carries type(exc).__name__ only.
    """
    if get_market_state(datetime.now(_ET)) == "open":
        return jsonify({"status": "market_open"}), 409

    if not _RETIREMENT_RUN_LOCK.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 409

    try:
        threading.Thread(
            target=_retirement_run_now_body,
            daemon=True,
            name="retirement-run-now",
        ).start()
    except Exception as exc:
        # The thread never started, so nothing else will release the lock.
        _RETIREMENT_RUN_LOCK.release()
        _daemon_log.error("ai_advisor_retirement_run: dispatch failed: %s", type(exc).__name__)
        return jsonify({"status": "error", "error": type(exc).__name__}), 500

    return jsonify({"status": "started"}), 202


@app.route("/ai-advisor/retirement/review/run", methods=["POST"])
def ai_advisor_retirement_review_run():
    """Start one on-demand symphony tenure review in the background.

    Reads no body. Refuses with 409 market_open while the market is open (the
    Composer API is shared with the live engine), before any slot is claimed.
    Claims the single run slot (409 when a review is running), runs the job on a daemon
    thread (a stop never waits on a review) and answers
    202 at once. A thread that cannot be built or started gives the slot back
    and answers 200 with the exception type name only. Advisory only: no
    Composer write, no trade primitive.
    """
    if get_market_state(datetime.now(_ET)) == "open":
        return jsonify({"status": "market_open"}), 409
    from advisors import tenure_review  # lazy (CC-2)

    owner = tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
    if not owner:
        return jsonify({"status": "already_running", "message": "a review is already running"}), 409
    try:
        threading.Thread(
            target=_tenure_review_job,
            args=("on_demand", owner),
            daemon=True,
            name="tenure-review-on-demand",
        ).start()
    except Exception as exc:
        tenure_review.REVIEW_RUN_STATE.release(owner)
        _daemon_log.error("Tenure review could not be started: %s", type(exc).__name__)
        return jsonify({"error": type(exc).__name__}), 200
    return jsonify({"status": "started", "message": "review started"}), 202


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
            s_dict["model"] = suggestions_response.serving_model
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


# A model id the panel posts back is recorded only when it is a plain id: the audit row
# must not store a forged or oversized value.
_PLAIN_MODEL_ID = re.compile(r"[A-Za-z0-9._:/+-]{1,128}")  # chars, 128 = max id length


def _audit_model_id(suggestion_data) -> str:
    """Model that served the suggestion being decided, else the registry resolution."""
    posted = suggestion_data.get("model") if isinstance(suggestion_data, dict) else None
    if isinstance(posted, str) and _PLAIN_MODEL_ID.fullmatch(posted):
        return posted
    return llm_request.default_generation_model()


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
        model_id=_audit_model_id(suggestion_data),
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
        model_id=_audit_model_id(suggestion_data),
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

    Chat is now an always-in-DOM slide panel on /ai-advisor, not a separate page. The redirect
    names the Chat tab (#chat) so the page opens on it.
    """
    return redirect(url_for("ai_advisor_tab") + "#chat", code=302)


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


def _is_feature_off_stub(obs: dict) -> bool:
    """Whether an advisor_observations row is a pure feature-off stub.

    The rule the AI Advisor page leaves such rows out by, and the rule
    ``database.count_advisor_observations_by_role`` counts by: verdict
    NOT_APPLICABLE and a raw_response that is a dict with ``feature_flag`` "off".
    """
    raw = obs.get("raw_response")
    return (
        obs.get("verdict") == "NOT_APPLICABLE"
        and isinstance(raw, dict)
        and raw.get("feature_flag") == "off"
    )


# The preview text of a row whose stored raw_response is not JSON (see
# database._parse_advisor_observation_row): the row is listed, and says so.
_OBSERVATION_UNREADABLE_TEXT = "Stored record could not be read."


def _build_advisor_observation_groups() -> list[dict]:
    """Per-producer observation groups for the AI Advisor Overview.

    One group per role in ``_ADVISOR_ROLES`` that has at least one kept row, in that
    order: ``{"role", "total", "rows"}``. ``rows`` are the role's newest
    ``_ADVISOR_OBSERVATIONS_PAGE_LIMIT`` rows, newest first, feature-off stubs left out
    (a row listed under an earlier role is not listed again). ``total`` is the role's
    whole kept-row count from ``database.count_advisor_observations_by_role``, never
    lower than the rows listed. Each row not from MARKET_PRISM gains ``_preview_text``
    (the humanised raw_response, or the unreadable text for a row whose stored
    raw_response is not JSON).

    Raises whatever the database reads raise; the caller shows the unavailable text.
    """
    per_role = [
        (
            role,
            database.get_advisor_observations_for_role(
                role, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT
            ),
        )
        for role in _ADVISOR_ROLES
    ]
    counts = database.count_advisor_observations_by_role(_ADVISOR_ROLES)

    try:
        from advisors.prism_render import humanize_obs_preview as _humanize_obs  # noqa: PLC0415
    except Exception:
        _humanize_obs = None  # Humanization failure must never crash the route.

    seen: set = set()
    groups: list[dict] = []
    for role, role_rows in per_role:
        kept: list[dict] = []
        for obs in role_rows:
            if obs["id"] in seen or _is_feature_off_stub(obs):
                continue
            seen.add(obs["id"])
            kept.append(obs)
        if not kept:
            continue
        for obs in kept:
            if obs.get("advisor_role") == "MARKET_PRISM":
                continue
            if obs.get("raw_response_unreadable"):
                obs["_preview_text"] = _OBSERVATION_UNREADABLE_TEXT
            elif _humanize_obs is not None:
                try:
                    obs["_preview_text"] = _humanize_obs(obs.get("raw_response"))
                except Exception:
                    pass  # The cell then reads blank; the row still renders.
        groups.append({"role": role, "total": max(counts.get(role, 0), len(kept)), "rows": kept})
    return groups


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

# The objectives an autotune card has a name for. A spec bundle can carry an
# objective_kind this code does not recognise; the route reports that exactly
# as it reports a run with no readable bundle (null), so no card ever prints a
# raw stored value as if it were a named objective.
_AUTOTUNE_NAMED_OBJECTIVES = frozenset({"crra_eu", "sortino_loss_aversion"})


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

    UX data-truth AC-1/AC-5: each row additively gains two more keys, both
    always present and null when they cannot be named:

    * `objective` -- the objective the run optimised, read from its spec bundle
      through database.get_autotune_run_objectives (the same rule the optimizer
      applies). One of `crra_eu` / `sortino_loss_aversion`; null when the run has
      no readable bundle or its bundle selects a value this code has no name for
      (the card then prints its generic score label, never the raw value).
    * `symphony_name` -- the symphony's display name from the live state, found
      by the row's normalized key; null when the live state does not hold it
      (the card then shows the stored key). The state is read once per request.
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

        objectives: dict = {}
        live_names: dict[str, str] = {}
        if rows:
            try:
                objectives = database.get_autotune_run_objectives(
                    [r["id"] for r in rows if r.get("id") is not None]
                )
            except Exception as exc:
                _daemon_log.warning(
                    "autotune-runs objective lookup skipped: %s", type(exc).__name__
                )
            try:
                live_names = _symphony_display_names_by_key(database.load_state())
            except Exception as exc:
                _daemon_log.warning(
                    "autotune-runs symphony-name lookup skipped: %s", type(exc).__name__
                )

        normalized = []
        for r in rows:
            n = _normalize_autotune_row(r)
            n["never_adopted_streak"] = never_adopted_streaks[r["symphony_id"]]
            objective = objectives.get(r.get("id"))
            n["objective"] = objective if objective in _AUTOTUNE_NAMED_OBJECTIVES else None
            symphony_key = r["symphony_id"]
            n["symphony_name"] = (
                live_names.get(database.normalize_name(symphony_key))
                if isinstance(symphony_key, str)
                else None
            )
            normalized.append(n)
        return jsonify(normalized)
    finally:
        _ro_conn.close()


if __name__ == "__main__":
    # UTF-8, line-buffered stdout (emoji/non-Latin-1 chars must not crash on Windows cp1252, and
    # journald must see each line at once) plus the timing-line handler.  Guarded to __main__ so
    # pytest's capture is not affected when this module is imported during test collection.
    timing_log.configure_daemon_output()

    # Enforce daemon singleton BEFORE starting Flask or the scheduler thread.
    # If another Planet Stopper is alive this call prints an error and exits non-zero.
    # If a stale pidfile exists (ungraceful prior kill) it is overwritten cleanly.
    _acquire_daemon_singleton(_PIDFILE_PATH)

    # Seed bot_state with baseline symphony entries before the scheduler starts.
    # Lazy import avoids any circular-import risk at module level; the call is a
    # no-op when entries already exist (idempotent — AC-2) and never raises (AC-4).
    from alpha_bot_execution import ensure_bot_state_seeded  # noqa: PLC0415

    ensure_bot_state_seeded()

    # One-time flatten of the stored close-snapshot chain; a failure must not block start.
    try:
        database.repair_close_snapshot_chain()
    except Exception as _repair_exc:
        logging.error("close snapshot repair raised: %s", type(_repair_exc).__name__)

    # Fill the account totals off-thread so the first polls are not value-weighted.
    _start_account_refresh("startup")

    # Start the scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    _start_model_warmup()
    port = int(os.environ.get("PORT", 5000))
    print(f"\nStarting Alpha Bot Control Center at http://localhost:{port}\n")

    # Disable use_reloader to ensure the background thread runs once and only once
    app.run(port=port, debug=False, use_reloader=False)
