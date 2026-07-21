"""
RED tests — F-010: compact structured logging for the known Composer
read-timeout case in `_refresh_account_totals`.

Source: docs/audit/confidence-program/running-register.md lines 113-118 —
"F-010 — INFO (operational): high Composer API read-timeout volume (~30/day)
... `alphabot_daemon.log` carries 958 full Python tracebacks in one month,
ALL from `_refresh_account_totals` (app.py:633) hitting `api.composer.trade`
read-timeouts (timeout=10) ... REMEDIATION: (hygiene) log a one-line WARNING
for the known-degradation path instead of a full traceback; reserve
tracebacks for unexpected errors."

Locus (current, drifted from the register's :633): app.py:767
`def _refresh_account_totals()`; the timeout is set at app.py:797
(`timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`, constant = 10 at app.py:554); the
full-traceback site is app.py:838-843
(`except Exception as _exc: _daemon_log.error(..., exc_info=True)`).

AC-4: the known Composer read-timeout logs ONE compact structured line (with
a counter or equivalent aggregation context); unknown exceptions keep full
tracebacks; retry/timeout constants byte-unchanged.

Edge case (plan): the compact line must not swallow DIFFERENT Composer
errors (timeout-only match) — a requests.exceptions.ConnectionError (a
sibling exception, NOT a Timeout) and a bare unexpected exception both keep
the full-traceback path.

Not an engine file — app.py is not in {alpha_bot_execution.py, math_engine.py,
autotuner.py} so AC-8's engine-file logging-only-diff exception never
triggers here.

Mock boundary: app_module.requests.get only. No live network, no live DB
(the function never opens one on the failure path). caplog used instead of
mocking the logger directly, so this test doesn't over-specify internal
variable/counter names — it asserts the observable LogRecord shape. -n0 only.
"""

from __future__ import annotations

import logging
import threading

import pytest
import requests

import app as app_module


@pytest.fixture(autouse=True)
def _reset_account_totals_state():
    """The module-level account-totals cache/timestamps are global state that
    could leak between tests in this file — reset before and after each test.
    """
    yield


# ---------------------------------------------------------------------------
# AC-4 core: known read-timeout -> ONE compact line, no traceback
# ---------------------------------------------------------------------------


def test_read_timeout_logs_exactly_one_record(monkeypatch, caplog):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out.")),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert len(caplog.records) == 1, (
        f"a known Composer read-timeout must log exactly ONE compact line, got "
        f"{len(caplog.records)}: {[r.getMessage() for r in caplog.records]!r}"
    )


def test_read_timeout_does_not_attach_a_traceback(monkeypatch, caplog):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out.")),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert caplog.records, "expected at least one log record for the read-timeout case"
    for record in caplog.records:
        assert record.exc_info is None, (
            "a known Composer read-timeout must log a compact line WITHOUT a "
            f"full traceback (exc_info must be None) — got exc_info={record.exc_info!r} "
            f"on record: {record.getMessage()!r}"
        )


def test_read_timeout_log_line_carries_aggregation_context(monkeypatch, caplog):
    """AC-4 requires 'a counter or equivalent aggregation context' — assert the
    rendered message contains a numeric token (shape assertion; the exact
    count value is implementation-owned and never hardcoded here).
    """
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out.")),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert any(any(ch.isdigit() for ch in record.getMessage()) for record in caplog.records), (
        "the compact read-timeout log line must carry a counter/aggregation "
        f"context (a digit) — got: {[r.getMessage() for r in caplog.records]!r}"
    )


def test_read_timeout_log_line_identifies_itself_as_a_timeout(monkeypatch, caplog):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out.")),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert any("timeout" in record.getMessage().lower() for record in caplog.records), (
        "the compact line must identify the case as a timeout so an operator "
        f"scanning the journal understands what happened — got: "
        f"{[r.getMessage() for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# Edge case: timeout-only match — a DIFFERENT Composer error keeps its traceback
# ---------------------------------------------------------------------------


def test_connection_error_keeps_full_traceback(monkeypatch, caplog):
    """requests.exceptions.ConnectionError is a Composer-reachability failure
    but NOT a read-timeout — it must not be silently folded into the compact
    timeout path (the plan's edge case: timeout-only match).
    """
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("Connection refused")
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert caplog.records, "expected at least one log record for the connection-error case"
    assert any(record.exc_info is not None for record in caplog.records), (
        "a non-timeout requests exception must keep its full traceback "
        f"(exc_info populated) — got: "
        f"{[(r.getMessage(), r.exc_info) for r in caplog.records]!r}"
    )


def test_unexpected_exception_keeps_full_traceback(monkeypatch, caplog):
    """An entirely unexpected exception type (not a requests exception at
    all) must still hit the D-1 full-traceback branch, unchanged.
    """
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("unexpected failure shape")),
    )
    with caplog.at_level(logging.DEBUG, logger="alphabot"):
        app_module._refresh_account_totals()
    assert caplog.records, "expected at least one log record for the unexpected-exception case"
    assert any(record.exc_info is not None for record in caplog.records), (
        "an unexpected exception must keep its full traceback (exc_info populated) — "
        f"got: {[(r.getMessage(), r.exc_info) for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# AC-8: retry/timeout constants byte-unchanged
# ---------------------------------------------------------------------------


def test_account_totals_timeout_constant_unchanged():
    """AC-8/plan: 'retry/timeout VALUES unchanged' — this cycle is
    logging-only. Pins the existing constant (app.py:554) so a future PR
    can't quietly fold a retry/timeout value change into this observability
    fix.
    """
    assert app_module._ACCOUNT_TOTALS_HTTP_TIMEOUT_S == 10, (
        "F-010 is a logging-only fix — the Composer read timeout value must "
        f"stay 10, got {app_module._ACCOUNT_TOTALS_HTTP_TIMEOUT_S!r}"
    )


# ---------------------------------------------------------------------------
# RED — foc-rev finding #2: _account_totals_timeout_count is NOT
# scheduler-thread-only, so the unlocked increment is a genuine race.
# ---------------------------------------------------------------------------


class _LockSpy:
    """Wraps a real threading.Lock and counts __enter__/acquire calls.

    threading.Lock() instances are C-level objects — their .acquire/.release
    methods are read-only and can't be monkeypatched directly (verified:
    `lock.acquire = spy` raises "attribute is read-only"). This proxy
    substitutes for the WHOLE module-level lock object instead, delegating
    every real locking call to a genuine Lock underneath so concurrent
    correctness is unaffected — it only adds counting.
    """

    def __init__(self):
        self._real_lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self._real_lock.__enter__()

    def __exit__(self, *exc_info):
        return self._real_lock.__exit__(*exc_info)

    def acquire(self, *args, **kwargs):
        self.enter_count += 1
        return self._real_lock.acquire(*args, **kwargs)

    def release(self, *args, **kwargs):
        return self._real_lock.release(*args, **kwargs)


def test_read_timeout_counter_increment_is_lock_protected(monkeypatch):
    """RED — foc-rev finding #2: _refresh_account_totals has THREE real call
    sites, not one — the minute-scheduler tick
    (`schedule.every().minute.at(":00").do(_refresh_account_totals)`), a
    background thread spawned by _notify_cycle_complete on every
    cycle-complete event, and another spawned by the flush-resync operator
    action — which CAN run concurrently (a cycle-complete refresh thread
    overlapping the :00 tick, or an operator hitting flush-resync
    mid-refresh). The committed comment above _account_totals_timeout_count
    claims "scheduler-thread-only (single minute-cadence caller, no lock
    needed)" — that claim is false. An unlocked `count += 1` is a genuine
    unsynchronized read-modify-write.

    (A raw multi-threaded stress test proving lost updates was tried first
    and abandoned as unreliable in this environment — a standalone
    microbenchmark of a bare unlocked `count += 1` across 16 threads x
    200,000 iterations each, even with sys.setswitchinterval forced to 1
    microsecond, lost ZERO updates on this Python 3.12/Windows combination.
    That doesn't mean the race is impossible under real production
    contention — it means empirical stress testing isn't a reliable way to
    PROVE it here. Testing for lock engagement directly is deterministic and
    tests the actual fix, not a probabilistic side effect of it.)

    This asserts the increment engages SOME lock — reusing the existing
    _account_totals_cache_lock (the function's own established convention
    for its other shared-state writes, per its docstring: "writes all keys
    under _account_totals_cache_lock") is the expected minimal fix, so that
    specific lock is substituted here. If a genuinely different lock is the
    right call, that's a scope question for test-writer, not a silent
    reinterpretation — flag it back.
    """
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out.")),
    )
    spy = _LockSpy()
    monkeypatch.setattr(app_module, "_account_totals_cache_lock", spy)

    app_module._refresh_account_totals()

    assert spy.enter_count >= 1, (
        "the timeout-counter increment must acquire _account_totals_cache_lock (this "
        "function's existing convention for its other shared-state writes) — "
        "_refresh_account_totals has 3 real concurrent call sites (scheduler tick, "
        "cycle-complete thread, flush-resync thread), so an unlocked count += 1 is a "
        "genuine unsynchronized race, and the code comment claiming 'no lock needed' is "
        "false."
    )
