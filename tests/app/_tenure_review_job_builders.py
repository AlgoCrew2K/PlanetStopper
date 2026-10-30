"""TEST-ONLY builders for the tests that drive the review run path (job, tick, route).

Nothing here is imported by production code.

Four groups live here:

1. Stand-ins for the machinery a run uses: threads that run their target on ``start()``
   (with or without the arguments they were given), a real thread that is also recorded, threads
   that refuse to be built or started, and a runner that blocks until the test lets it finish.
2. A frozen clock for ``app.datetime`` (the run reads US Eastern time once, at its start).
3. A fake runner answer (``patch_runner``) built on the real result types, for the tests
   that exercise what the job does with a run and not the Composer reads. Its recommendation
   dicts are whatever the caller passes in; its entries are the minimal retire-candidate rows.
4. The Composer world of the end-to-end tests: the captured statistics, with invented display
   names, and one derived series (``twin_series_reply``) that yields a passing redundancy pair,
   because the captured series are not correlated enough to produce one.

Derived forms are labelled where they are made. The twin series is the captured series of one
live symphony with every daily return multiplied by a constant and a small deterministic
wobble added: it is a constructed series (never a captured one) whose dates, start value and
shape come from the capture, and whose correlation with its source is computed by the
tests from the series themselves.
"""

from __future__ import annotations

import builtins
import contextlib
import copy
import logging
import math
import os
import threading
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import advisors.tenure_review as tr
import app as app_module
import database as db_module
from advisors import tenure_review_runner as runner_module
from tests.advisors import _composer_http_router as h
from tests.advisors import _tenure_review_builders as b
from tests.app import _tenure_review_panel_builders as pb
from tests.app.test_held_basis_route_convergence import _frozen_datetime_class

ET = ZoneInfo("America/New_York")

# A Tuesday before the open: the latest session whose regular session had closed is Monday
# 2026-10-19, which is also the last point of every captured series.
NOW_ET = datetime(2026, 10, 20, 3, 45, tzinfo=ET)
AS_OF = date(2026, 10, 19)
WRITTEN_ON = "2026-10-20"  # NOW_ET's date, which is not the as-of day

ACCOUNT_KEYS = ("ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD")
THRESHOLD_KEY = "TENURE_REVIEW_MIN_TRADING_DAYS"
ROLE_RUN = tr.TENURE_REVIEW_RUN_ROLE
ROLE_REC = "RETIREMENT_RECOMMENDATION"


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def freeze_app_clock(monkeypatch, instant: datetime = NOW_ET):
    """Make ``app.datetime.now(tz)`` answer ``instant`` in ``tz`` for the rest of the test."""

    frozen = _frozen_datetime_class(instant)
    monkeypatch.setattr(app_module, "datetime", frozen)
    return frozen


# ---------------------------------------------------------------------------
# Threads and executors
# ---------------------------------------------------------------------------


class ImmediateThread:
    """Stands in for ``threading.Thread``: ``start()`` calls the target with no arguments, on the
    caller's thread. A real thread would pass ``args`` and ``kwargs`` on; this one drops them, so
    a tick that hands the run's owner token to its thread as an argument (and not bound into the
    target) shows up as a run that never starts."""

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None):
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


# The real class, captured at import time before any test replaces ``threading.Thread``: a test
# that needs a real thread of its own (to post two requests together, say) builds it from this.
REAL_THREAD = threading.Thread


class StartedThread:
    """Stands in for ``threading.Thread`` on the route: ``start()`` calls ``target(*args, **kwargs)``
    on the caller's thread, with the arguments the thread was given, and the thread is recorded
    (with its ``daemon`` flag and name) so a test can read how production built it."""

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None):
        self._target = target
        self._args = tuple(args)
        self._kwargs = dict(kwargs or {})
        self.name = name
        self.daemon = daemon

    def start(self):
        self.started.append(self)
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def is_pool_worker(target) -> bool:
    """True when ``target`` is the loop of a ``concurrent.futures`` pool worker."""
    return getattr(target, "__module__", "").startswith("concurrent.futures")


def _target_of(args: tuple, kwargs: dict):
    return kwargs.get("target", args[1] if len(args) > 1 else None)


def install_inline_threads(monkeypatch) -> list[StartedThread]:
    """Replace ``threading.Thread`` for the rest of the test by a subclass of ``StartedThread``;
    the returned list collects every thread whose ``start()`` was called, in order.

    It stays a class: a library imported while it is in place (the panel page imports one lazily)
    may subclass ``threading.Thread``. The loop of a ``concurrent.futures`` pool worker never
    returns, so a pool worker is built as the real thread it is: code that still submits its job
    to a pool gets a worker that works, and the test fails on what it asserts and does not hang."""
    started: list[StartedThread] = []

    class Inline(StartedThread):
        def __new__(cls, *args, **kwargs):
            if is_pool_worker(_target_of(args, kwargs)):
                return REAL_THREAD(*args, **kwargs)
            return super().__new__(cls)

    Inline.started = started
    monkeypatch.setattr(threading, "Thread", Inline)
    return started


def install_refusing_threads(
    monkeypatch, exc: BaseException, *, where: str = "start"
) -> list[tuple[str, object]]:
    """Replace ``threading.Thread`` by a class whose construction (``where="construct"``) or
    ``start()`` (``where="start"``) raises ``exc``. The returned list records
    ``("construct", target)`` and ``("start", target)`` for each stage that was reached."""
    reached: list[tuple[str, object]] = []

    class Refusing:
        def __init__(self, *args, **kwargs):
            self._target = _target_of(args, kwargs)
            reached.append(("construct", self._target))
            if where == "construct":
                raise exc

        def start(self):
            reached.append(("start", self._target))
            raise exc

    monkeypatch.setattr(threading, "Thread", Refusing)
    return reached


class RecordingThread(threading.Thread):
    """A real thread that is also recorded, so a test can join every thread a tick started."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)


def install_recording_threads(monkeypatch) -> list[RecordingThread]:
    """Replace ``threading.Thread`` by ``RecordingThread`` (a real thread) for the rest of the
    test; the returned list collects every thread built since."""
    RecordingThread.instances.clear()
    monkeypatch.setattr(threading, "Thread", RecordingThread)
    return RecordingThread.instances


def join_threads(threads, timeout: float = 30.0) -> None:
    """Wait for each recorded thread to end."""
    for thread in list(threads):
        thread.join(timeout=timeout)
        assert not thread.is_alive(), "a review thread did not end"


# ---------------------------------------------------------------------------
# A runner answer for the tests that do not exercise the Composer reads
# ---------------------------------------------------------------------------


def minimal_entry(symphony_id: str, display_name: str | None = None, verdict="retire_candidate"):
    return {
        "symphony_id": symphony_id,
        "display_name": display_name or symphony_id,
        "verdict": verdict,
    }


def make_outcome(
    recs: list[dict],
    *,
    run_id: str,
    trigger: str,
    now_et: datetime,
    entries: list[dict] | None = None,
    threshold: tr.ThresholdSetting | None = None,
) -> runner_module.RunOutcome:
    """A completed run over the real result types: one minimal entry per recommendation
    unless ``entries`` is given."""
    as_of = tr.as_of_trading_day(now_et)
    threshold = threshold or tr.ThresholdSetting(tr.DEFAULT_MIN_TENURE_TRADING_DAYS, None)
    if entries is None:
        entries = [minimal_entry(r["candidate_id"], r.get("candidate_name")) for r in recs]
    result = tr.ReviewResult(
        run_id=run_id,
        trigger=trigger,
        started_at=now_et,
        as_of=as_of,
        threshold=threshold,
        composite_window={
            "first": "2026-04-21",
            "last": as_of.isoformat(),
            "days": threshold.value,
        },
        entries=entries,
        recommendations=recs,
    )
    return runner_module.RunOutcome(True, result, None)


_NO_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_such_env_dir", ".env")


def hide_environment(monkeypatch) -> None:
    """Point the app at an environment file that does not exist and clear every variable the
    run reads, so the developer's real file and variables are never consulted."""
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", _NO_ENV_FILE)
    for key in (*ACCOUNT_KEYS, THRESHOLD_KEY):
        monkeypatch.delenv(key, raising=False)


def stub_live_explainer(monkeypatch) -> None:
    """Replace the LLM explainer with canned text unless the test already replaced it, so a
    review job run by a test can never make a network call."""
    import advisors.retirement_explainer as explainer_module  # noqa: PLC0415

    current = explainer_module.explain_recommendation
    if getattr(current, "__module__", None) == explainer_module.__name__:
        monkeypatch.setattr(explainer_module, "explain_recommendation", lambda rec: "canned text")


def fake_runner(recs=(), *, entries=None, outcome=None, hook=None, calls=None):
    """A stand-in for ``run_tenure_review`` that answers a completed run over the real result
    types. ``recs`` is a list, or a callable returning one for each run (a fresh list each time,
    since the job completes the dicts in place). ``outcome``, when given, is returned as it is
    (an uncompleted ``RunOutcome``, say). ``hook(kwargs)`` runs before it answers (it may raise).
    ``calls`` collects the keyword arguments of every call."""

    def fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if hook is not None:
            hook(kwargs)
        if outcome is not None:
            return outcome
        wanted = recs() if callable(recs) else recs
        return make_outcome(
            copy.deepcopy(list(wanted)),
            run_id=kwargs["run_id"],
            trigger=kwargs["trigger"],
            now_et=kwargs["now_et"],
            entries=copy.deepcopy(entries),
            threshold=tr.parse_threshold(kwargs.get("threshold_raw")),
        )

    return fake


def patch_runner(
    monkeypatch, recs=(), *, entries=None, outcome=None, hook=None, isolate=True
) -> list[dict]:
    """Replace ``advisors.tenure_review_runner.run_tenure_review`` with ``fake_runner`` and, unless
    ``isolate`` is False (the caller already built its own environment), hide the environment.
    Returns the list of keyword-argument dicts the job called the runner with, one per call."""
    calls: list[dict] = []
    if isolate:
        hide_environment(monkeypatch)
    stub_live_explainer(monkeypatch)
    monkeypatch.setattr(
        runner_module,
        "run_tenure_review",
        fake_runner(recs, entries=entries, outcome=outcome, hook=hook, calls=calls),
    )
    return calls


class BlockingRunner:
    """A runner that signals it was entered and waits for the test before answering."""

    def __init__(self, recs=(), *, delegate=None):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict] = []
        self._recs = list(recs)
        self._delegate = delegate

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.set()
        assert self.release.wait(60), "the test never released the blocked runner"
        if self._delegate is not None:
            return self._delegate(**kwargs)
        return make_outcome(
            copy.deepcopy(self._recs),
            run_id=kwargs["run_id"],
            trigger=kwargs["trigger"],
            now_et=kwargs["now_et"],
        )

    def install(self, monkeypatch):
        stub_live_explainer(monkeypatch)
        monkeypatch.setattr(runner_module, "run_tenure_review", self)
        return self


# ---------------------------------------------------------------------------
# Environment and storage
# ---------------------------------------------------------------------------


def isolate_env(monkeypatch, tmp_path, *, accounts=(), threshold=None, os_env=None):
    """Point the app at a scratch environment file and clear every variable the run reads.

    ``accounts`` is a list of ``(key, value)`` written to the scratch file; ``threshold`` is
    the raw threshold text written there (``None`` leaves the key out); ``os_env`` is set in
    the process environment. The developer's real file is never read."""
    path = tmp_path / "scratch.env"
    for key in (*ACCOUNT_KEYS, THRESHOLD_KEY):
        monkeypatch.delenv(key, raising=False)
    lines = [f"{key}={value}" for key, value in accounts]
    if threshold is not None:
        lines.append(f"{THRESHOLD_KEY}={threshold}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    for key, value in (os_env or {}).items():
        monkeypatch.setenv(key, value)
    return path


def rewrite_env(path, *, accounts=(), threshold=None) -> None:
    lines = [f"{key}={value}" for key, value in accounts]
    if threshold is not None:
        lines.append(f"{THRESHOLD_KEY}={threshold}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def use_scratch_db(monkeypatch, tmp_path, name="review_run_path.db") -> str:
    db_path = str(tmp_path / name)
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


def rows(role: str) -> list[dict]:
    """Every stored row of a role, newest first."""
    return db_module.get_advisor_observations_for_role(role, limit=1000)


def run_rows() -> list[dict]:
    return rows(ROLE_RUN)


def rec_rows() -> list[dict]:
    return rows(ROLE_REC)


def all_row_ids() -> set[int]:
    conn = db_module.get_connection()
    try:
        return {r[0] for r in conn.execute("SELECT id FROM advisor_observations").fetchall()}
    finally:
        conn.close()


def new_rows_since(before: set[int]) -> list[dict]:
    """Every advisor_observations row written after ``before`` (a set of ids), as dicts."""
    conn = db_module.get_connection()
    try:
        cur = conn.execute(
            "SELECT id, advisor_role, subject_id, is_advisory_only FROM advisor_observations"
        )
        return [
            {"id": r[0], "advisor_role": r[1], "subject_id": r[2], "is_advisory_only": r[3]}
            for r in cur.fetchall()
            if r[0] not in before
        ]
    finally:
        conn.close()


def the_run_state():
    """The run state the app consults, looked up the way production must look it up."""
    return tr.REVIEW_RUN_STATE


def state_is_idle() -> bool:
    snap = the_run_state().snapshot()
    return snap["running"] is False and snap["running_since"] is None and snap["trigger"] is None


def taken_then_released() -> bool:
    """True when the single run slot can be taken now (and is given back)."""
    state = the_run_state()
    token = state.try_start("nightly")
    if not token:
        return False
    state.release(token)
    return True


# ---------------------------------------------------------------------------
# The Composer world of the end-to-end tests
# ---------------------------------------------------------------------------

# The six live symphonies of the captured statistics, renamed. The names are invented; the
# ids are the captured ones (real shape). Only the statistics ``name`` field is replaced.
GPAW_ID = "Gpaw3IhZghQPRE6AdEKx"  # eligible, but the capture holds no series for it
LW4Z_ID = "lW4ZzWuqR8tEO2DhXbil"  # eligible, but the capture holds no series for it
INVENTED_NAMES = {
    h.OLDEST_ID: "Copper Ridge Rotation",
    h.MID_ID: "Harbor Lights Momentum",
    h.BACKTEST_ID: "Slate Valley Hedge",
    h.YOUNGEST_ID: "Granite Pier Trend",
    GPAW_ID: "Willow Creek Balance",
    LW4Z_ID: "Birch Hollow Carry",
}

# Invented symphony id in the real shape (twenty alphanumerics) for the derived series.
TWIN_ID = "Tw1nM1rr0rXk9Pq2WzAb"
TWIN_NAME = "Lantern Hill Mirror"
TWIN_SCALE = 0.6
TWIN_WOBBLE = 0.0015


_ROUTERS: list = []
# Every call the guard refused since it was installed. The refusal also raises, but production
# code catches broad exceptions around its network calls and its client construction, so the
# raise alone can pass unseen; the record is what the teardown checks.
_REFUSED: list[str] = []


def block_live_calls(monkeypatch) -> None:
    """Make any real Anthropic client or outbound HTTP request fail the test.

    Each refused call is recorded and raises. A caller that catches the error (the explainer and
    the Composer clients do) hides the raise, so ``assert_no_unserved_requests`` fails the test
    from the record whether or not the error was caught. Installed before the URL router, which
    replaces the same ``requests`` entry points and so keeps serving the Composer shapes it
    knows; the router's own refusals are checked by the same teardown call."""
    import requests  # noqa: PLC0415

    import ai_advisor  # noqa: PLC0415

    def refusing(label: str):
        def refuse(*_args, **_kwargs):
            _REFUSED.append(label)
            raise AssertionError("a test reached a live network call or a real Anthropic client")

        return refuse

    _REFUSED.clear()
    monkeypatch.setattr(
        ai_advisor, "_build_client", refusing("ai_advisor._build_client"), raising=False
    )
    try:
        import anthropic  # noqa: PLC0415

        monkeypatch.setattr(anthropic, "Anthropic", refusing("anthropic.Anthropic"))
    except ImportError:
        pass
    for name in ("get", "post", "put", "patch", "delete", "head", "options", "request"):
        monkeypatch.setattr(requests, name, refusing(f"requests.{name}"))
    monkeypatch.setattr(
        requests.sessions.Session, "request", refusing("requests.sessions.Session.request")
    )
    _ROUTERS.clear()


def refused_live_calls() -> list[str]:
    """The labels of the calls the guard has refused so far (a copy)."""
    return list(_REFUSED)


def assert_no_unserved_requests() -> None:
    """Fail when the guard refused any call, or a URL router built by ``install_world`` had to
    refuse a request. Both records are cleared first, so one failure is reported once."""
    refused = list(_REFUSED)
    _REFUSED.clear()
    routers = list(_ROUTERS)
    _ROUTERS.clear()
    assert refused == [], (
        f"the test reached a live network call or a real Anthropic client {refused}; "
        "production code may have caught the error"
    )
    for router in routers:
        assert router.violations == [], (
            "the run made a request the Composer router does not serve: "
            f"{[(r.method, r.url) for r in router.violations]}"
        )


# ---------------------------------------------------------------------------
# The daemon's log file and the import system
# ---------------------------------------------------------------------------


class DaemonLog:
    """The records that reached a file handler of the daemon's log file, each once."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []

    def text(self) -> str:
        """One line per record: level, logger name and message."""
        return "\n".join(f"{r.levelname} {r.name} {r.getMessage()}" for r in self.records)

    def positions_naming(self, needle: str) -> list[int]:
        """The position, in the order the records reached the file, of each record whose message
        holds ``needle``."""
        return [i for i, r in enumerate(self.records) if needle in r.getMessage()]

    def positions_of_loggers(self, *names: str) -> list[int]:
        """The position of each record logged on one of the named loggers."""
        return [i for i, r in enumerate(self.records) if r.name in names]


@contextlib.contextmanager
def capturing_daemon_log(monkeypatch):
    """Yield a ``DaemonLog`` that receives every record the daemon's log file would receive.

    The daemon configures no root handler: its log file is written by the file handlers it
    attaches itself. Every file handler found anywhere in the logging tree that writes to the
    daemon's log file has its ``handle`` replaced by a recorder (nothing is written to the file),
    so a record is captured only if the logger it was emitted on reaches one of those handlers
    at its own level, which is the path it takes to the file. A record that reaches two such
    handlers (the module was loaded twice) is captured once. The root logger is held at its
    default level (``WARNING``) inside the block, so a module logger whose lines reach the file
    only because a test runner lowered the root level is not mistaken for one that delivers
    them."""
    log = DaemonLog()
    wanted = os.path.abspath(app_module.LOG_FILE)
    loggers = [logging.getLogger(), *logging.root.manager.loggerDict.values()]
    handlers = {
        id(h): h
        for lg in loggers
        if isinstance(lg, logging.Logger)
        for h in lg.handlers
        if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == wanted
    }
    assert handlers, "no file handler writes to the daemon's log file"

    def record_once(record) -> bool:
        if all(record is not seen for seen in log.records):
            log.records.append(record)
        return True

    for handler in handlers.values():
        monkeypatch.setattr(handler, "handle", record_once)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.WARNING)
    try:
        yield log
    finally:
        root.setLevel(previous)


# The loggers whose lines are the per-request record of a run (the Composer reads, the backtest
# POST, the runner's own warnings). None of them is a child of the daemon's logger.
RUN_PATH_LOGGER_NAMES = (
    "advisors.composer_portfolio_client",
    "advisors.composer_backtest_client",
    "advisors.tenure_review_runner",
)


def daemon_file_handlers() -> list[logging.FileHandler]:
    """Every file handler anywhere in the logging tree that writes to the daemon's log file."""
    wanted = os.path.abspath(app_module.LOG_FILE)
    loggers = [logging.getLogger(), *logging.root.manager.loggerDict.values()]
    found = {
        id(h): h
        for lg in loggers
        if isinstance(lg, logging.Logger)
        for h in lg.handlers
        if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == wanted
    }
    return list(found.values())


def handlers_a_record_of(logger_name: str) -> list[logging.Handler]:
    """The handlers a record logged on ``logger_name`` is handed to, in the order the logging
    module walks them (the logger's own, then each ancestor's while propagation is on). The
    test runner's own capture handlers are left out: they exist only under a test run, and this
    is about what the daemon process does."""
    handlers: list[logging.Handler] = []
    logger: logging.Logger | None = logging.getLogger(logger_name)
    while logger is not None:
        handlers.extend(h for h in logger.handlers if not type(h).__module__.startswith("_pytest"))
        if not logger.propagate:
            break
        logger = logger.parent
    return handlers


def writes_warnings_to_stderr(handler: logging.Handler) -> bool:
    """A stream handler (not a file handler) on standard error that passes a warning and nothing
    below it: what a logger without any handler gets from the logging module's last resort."""
    import sys  # noqa: PLC0415

    return (
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and getattr(handler, "stream", None) in (sys.stderr, sys.__stderr__)
        and handler.level == logging.WARNING
    )


def fail_imports_of(monkeypatch, *modules: str) -> None:
    """Make an import statement that names one of ``modules`` (dotted, by ``import a.b``,
    ``from a.b import x`` or ``from a import b``) raise ``ImportError`` for the rest of the test."""
    real_import = builtins.__import__
    wanted = set(modules)

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        named = {name, *(f"{name}.{item}" for item in (fromlist or ()))}
        if level == 0 and named & wanted:
            raise ImportError(f"planted import failure: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)


def twin_series_reply(source_id: str = h.MID_ID) -> h.Reply:
    """A derived series: the source's captured dates and start value, every daily return
    scaled by ``TWIN_SCALE`` with ``TWIN_WOBBLE * sin(1.3 * day index)`` added."""
    base = h.record_body(h.series_record_name(source_id))
    values = base["deposit_adjusted_series"]
    twin = [values[0]]
    for i in range(1, len(values)):
        change = values[i] / values[i - 1] - 1
        twin.append(twin[-1] * (1 + TWIN_SCALE * change + TWIN_WOBBLE * math.sin(1.3 * i)))
    return h.Reply(
        body={
            "epoch_ms": list(base["epoch_ms"]),
            "series": list(twin),
            "deposit_adjusted_series": list(twin),
        }
    )


def twin_series_values(source_id: str = h.MID_ID) -> dict[str, float]:
    """ISO date to value of the derived series (the oracle reads these, not the reply)."""
    body = twin_series_reply(source_id).body
    return {
        h.utc_date(ms): value
        for ms, value in zip(body["epoch_ms"], body["deposit_adjusted_series"], strict=True)
    }


def statistics_reply(
    *, invested_since: dict[str, str] | None = None, twin: bool = False, names=None
) -> h.Reply:
    """The captured statistics with invented display names; ``invested_since`` replaces the
    named entries' date (a derived form, the only other field changed); ``twin`` adds an
    entry copied from the mid-age symphony under the derived id."""
    body = h.statistics_body()
    names = INVENTED_NAMES if names is None else names
    for entry in body["symphonies"]:
        entry["name"] = names.get(entry["id"], entry["name"])
        if invested_since and entry["id"] in invested_since:
            entry["invested_since"] = invested_since[entry["id"]]
    if twin:
        mid = next(e for e in body["symphonies"] if e["id"] == h.MID_ID)
        copy_of = copy.deepcopy(mid)
        copy_of["id"] = TWIN_ID
        copy_of["name"] = TWIN_NAME
        if invested_since and TWIN_ID in invested_since:
            copy_of["invested_since"] = invested_since[TWIN_ID]
        body["symphonies"].append(copy_of)
    return h.Reply(body=body)


def install_world(
    monkeypatch, *, twin: bool = False, invested_since: dict[str, str] | None = None
) -> h.ComposerRouter:
    """Serve the captured Composer records through the URL router, with the renamed
    statistics (and the derived twin when asked)."""
    router = h.ComposerRouter().install(monkeypatch)
    _ROUTERS.append(router)
    router.statistics_default = statistics_reply(invested_since=invested_since, twin=twin)
    if twin:
        router.set_series(TWIN_ID, twin_series_reply())
    return router


def install_clean_world(
    monkeypatch, *, twin: bool = True, invested_since: dict[str, str] | None = None
) -> h.ComposerRouter:
    """``install_world`` with every read the run makes answered.

    The router's default for a definition no record exists for is a refusal (HTTP 404); here the
    one captured definition is served for every symphony of the roster too, so no request of the
    run fails. A symphony the capture holds an empty series answer for stays so: an empty list is
    an answer, not a failed read."""
    router = install_world(monkeypatch, twin=twin, invested_since=invested_since)
    captured = router.definition_by_symphony[h.BACKTEST_ID]
    for symphony_id in (*stored_and_listed_ids(), TWIN_ID):
        router.definition_by_symphony.setdefault(symphony_id, captured)
    return router


def stored_and_listed_ids() -> list[str]:
    return [e["id"] for e in h.statistics_body()["symphonies"]]


# ---------------------------------------------------------------------------
# What the operator sees after a run: the API answer and the panel
# ---------------------------------------------------------------------------


def api(client) -> dict:
    """``GET /api/retirement-recommendations`` parsed as strict JSON (200 required)."""
    response = client.get("/api/retirement-recommendations")
    assert response.status_code == 200, f"the API answered {response.status_code}"
    return pb.strict_json(response)


def api_after_run(client) -> dict:
    """``api`` for a test that expects a review run to be stored: fails with that reason, not
    with a missing key further on."""
    data = api(client)
    assert data["review_run"] is not None, "no review run is stored: the run did not complete"
    return data


def assert_review_ran(calls: list, who: str = "the worker") -> None:
    """The stand-in for the review run was called: ``who`` reached the review run at all."""
    assert calls, f"{who} never reached the review run (run_tenure_review was not called)"


def entries_by_id(data: dict) -> dict[str, dict]:
    return {e["symphony_id"]: e for e in data["review_entries"]}


def panel(client):
    """The Retirement Recommendations panel of the AI Advisor page, as a node tree."""
    response = client.get("/ai-advisor")
    assert response.status_code == 200, f"the page answered {response.status_code}"
    return pb.panel_of(response.get_data(as_text=True))


def seed_live_names(include_twin: bool = True) -> None:
    """Write the engine's per-symphony state with the invented display names (no holdings), so
    a recommendation's candidate and sibling names resolve to names and not to raw ids."""
    names = dict(INVENTED_NAMES)
    if include_twin:
        names[TWIN_ID] = TWIN_NAME
    db_module.save_state({sid: {"name": name, "logic_holdings": {}} for sid, name in names.items()})
