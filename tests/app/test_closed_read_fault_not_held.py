"""A read that fails while a closed market's answer is built is never kept until Monday.

The closed-poll hold (`_ClosedHold`) keeps a build that did not record a failure. The shadow readers
in `analytics` and `app` catch a database error and hand back `None`, an empty list or "no symphony
is gone", which the build cannot tell from a world with no history, so one locked read was kept as
the answer to the next snapshot: a blank hero chart, or a card with a missing leg, all weekend, and
a different answer after a restart. The rule: a figure with a part missing is answered, never held.

The fault is injected where a real one happens, at the SQL statement. Every connection the process
opens is a subclass whose cursor raises `sqlite3.OperationalError("database is locked")` for ONE
chosen read of `shadow_history`; every other statement, before and after, is the real database.
Nothing in `analytics` or `app` is patched, so a reader that swallows the error is exercised the
way production meets it (a test that patches the reader to raise never reaches the `except` that
swallows).

The reads are enumerated from the code, not listed here: a fault-free build of each route runs once
on a cold hold with the same connection class recording every read of the table, and each distinct
read (statement and parameters) of that build is then failed once, in a build of its own. So a read
added to any path under `_closed_poll_view`, `_closed_hero_body` or `_build_windowed_strip` is
covered by the same test without an edit. Expected answers are never typed in: they are the bytes
of a fault-free build of the same world.

Not covered: a fault while the connection itself is opened or its committed view is created
(`_committed_shadow_conn` documents that those errors propagate: a response without its committed
view must fail), and reads of the state database (`database.load_state`), which are not
`shadow_history` reads.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date

import pytest

import app as app_module
from tests.app._closed_session_world import ClosedWorld, build_history_world
from tests.app.test_closed_poll_hold import (
    CLOSED_INSTANTS,
    CLOSED_ROUTES,
    DATABASE_LOCKED,
    read_closed_routes,
)

# Routes whose held result is shared: a poll of one is answered from the other's build.
SHARED_HOLD = {"/api/state": ("/",), "/": ("/api/state",)}
# A second window, so the token-specific reads (the cutoff, the window's own series) are built too.
FAULT_ROUTES = (*CLOSED_ROUTES, "/api/strip/ytd", "/api/hero-chart/ytd")


def _is_shadow_read(sql: str) -> bool:
    head = " ".join(sql.split()).lower()
    return "shadow_history" in head and head.startswith(("select", "with"))


@dataclass
class ReadFaults:
    """Counts the reads of `shadow_history` on every connection of one thread and fails one."""

    thread: str
    reads: list[tuple[str, str]] = field(default_factory=list)
    fail: tuple[str, str] | None = None
    times: int | None = None
    failed: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def gate(self, sql: str, params: object) -> None:
        if threading.current_thread().name != self.thread or not _is_shadow_read(sql):
            return
        with self._lock:
            read = (" ".join(sql.split()), repr(params))
            self.reads.append(read)
            if read == self.fail and (self.times is None or len(self.failed) < self.times):
                self.failed.append(read[0])
                raise sqlite3.OperationalError(DATABASE_LOCKED)

    def begin(self, fail: tuple[str, str] | None = None, times: int | None = 1) -> None:
        """Start a build: read `fail` (a statement and its parameters) raises for its first
        `times` runs, every run when `times` is None; with no `fail` nothing raises."""
        with self._lock:
            self.reads.clear()
            self.failed.clear()
            self.fail = fail
            self.times = times

    def install(self, monkeypatch) -> ReadFaults:
        real_connect = sqlite3.connect
        plan = self

        class _Cursor(sqlite3.Cursor):
            def execute(self, sql, parameters=(), /):
                plan.gate(sql, parameters)
                return super().execute(sql, parameters)

        class _Connection(sqlite3.Connection):
            # Connection.execute runs its statement without calling Cursor.execute, so it is
            # gated here and a cursor's own execute above: each statement reaches the gate once.
            def execute(self, sql, parameters=(), /):
                plan.gate(sql, parameters)
                return super().execute(sql, parameters)

            def cursor(self, factory=None):
                return super().cursor(factory or _Cursor)

        def connect(*args, **kwargs):
            kwargs.setdefault("factory", _Connection)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", connect)
        return self

    def distinct_reads(self) -> list[tuple[str, str]]:
        """Every distinct read (statement, parameters) of the last build, in the order first run."""
        return list(dict.fromkeys(self.reads))


def world(monkeypatch, tmp_path) -> tuple[ClosedWorld, ReadFaults]:
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    return cw, ReadFaults(threading.current_thread().name).install(monkeypatch)


def short(sql: str) -> str:
    return sql if len(sql) <= 150 else sql[:147] + "..."


# How long the lock lasts inside the build that meets it: one read fails (a brief lock), or every
# run of that read does (a recovery that outlasts the whole build, so a fallback that reads the
# same rows meets it too).
LOCKS = {"one run": 1, "every run": None}


@pytest.mark.parametrize("lock", sorted(LOCKS))
@pytest.mark.parametrize("path", FAULT_ROUTES)
@pytest.mark.parametrize("market_state", sorted(CLOSED_INSTANTS))
def test_a_poll_that_met_a_failed_read_is_not_kept(monkeypatch, tmp_path, path, market_state, lock):
    cw, faults = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS[market_state])
    app_module._CLOSED_HOLD.clear()
    faults.begin()
    reference = cw.raw(path)
    reads = faults.distinct_reads()
    assert reads, f"fixture sanity: a cold build of {path} read no shadow_history row"
    others = {other: cw.raw(other) for other in SHARED_HOLD.get(path, ())}

    problems: list[str] = []
    for number, read in enumerate(reads):
        statement = short(read[0])
        app_module._CLOSED_HOLD.clear()
        faults.begin(read, LOCKS[lock])
        answered = cw.world.client.get(path)
        fired = list(faults.failed)
        faults.begin()
        if not fired:
            problems.append(f"read {number}: the fault was not reached ({statement})")
            continue
        if answered.status_code != 200:
            problems.append(
                f"read {number}: the poll that met the fault answered {answered.status_code}, "
                f"not 200 ({statement})"
            )
        again = cw.world.client.get(path)
        if again.status_code != 200 or again.get_data() != reference:
            problems.append(
                f"read {number}: the next poll of {path} did not answer a fault-free build's "
                f"bytes, so the build that met the fault was kept ({statement})"
            )
        for other, expected in others.items():
            if cw.raw(other) != expected:
                problems.append(
                    f"read {number}: {other}, which shares the held result of {path}, answered "
                    f"other than a fault-free build ({statement})"
                )
    assert not problems, (
        f"{path} ({market_state}, lock on {lock}): {len(problems)} of {len(reads)} distinct "
        "reads left a degraded answer held:\n" + "\n".join(problems)
    )


@pytest.mark.parametrize("lock", sorted(LOCKS))
@pytest.mark.parametrize("market_state", sorted(CLOSED_INSTANTS))
def test_a_warm_up_that_met_a_failed_read_holds_nothing_degraded(
    monkeypatch, tmp_path, market_state, lock
):
    """The warm-up builds every held result ahead of the first poll, over one shared connection;
    one failed read in it must leave every route answering what a fault-free build does."""
    cw, faults = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS[market_state])
    app_module._CLOSED_HOLD.clear()
    reference = {path: cw.raw(path) for path in FAULT_ROUTES}
    app_module._CLOSED_HOLD.clear()
    faults.begin()
    app_module._warm_closed_results()
    reads = faults.distinct_reads()
    assert reads, "fixture sanity: a cold warm-up read no shadow_history row"

    problems: list[str] = []
    for number, read in enumerate(reads):
        statement = short(read[0])
        app_module._CLOSED_HOLD.clear()
        faults.begin(read, LOCKS[lock])
        app_module._warm_closed_results()
        fired = list(faults.failed)
        faults.begin()
        if not fired:
            problems.append(f"read {number}: the fault was not reached ({statement})")
            continue
        wrong = []
        for path in FAULT_ROUTES:
            answered = cw.world.client.get(path)
            if answered.status_code != 200 or answered.get_data() != reference[path]:
                wrong.append(path)
        if wrong:
            problems.append(
                f"read {number}: after the warm-up, {wrong} answered other than a fault-free "
                f"build ({statement})"
            )
    assert not problems, (
        f"{market_state}, lock on {lock}: {len(problems)} of {len(reads)} distinct warm-up reads "
        "left a degraded answer held:\n" + "\n".join(problems)
    )


@pytest.mark.parametrize("path", CLOSED_ROUTES[:3])
def test_a_symphony_with_no_history_rows_is_a_complete_build_and_is_held(
    monkeypatch, tmp_path, path
):
    """The fix must not read an absence as a failure: a symphony with no shadow rows at all (a new
    one) is answered without a leg and the answer is held, so a weekend of polls is lookups."""
    cw, faults = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS["closed_frozen"])
    app_module._CLOSED_HOLD.clear()
    with sqlite3.connect(app_module.database._db_file()) as conn:
        newest = conn.execute(
            "SELECT symphony_id FROM shadow_history GROUP BY symphony_id "
            "ORDER BY MIN(ts_utc) DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute("DELETE FROM shadow_history WHERE symphony_id = ?", (newest,))
    first = cw.raw(path)
    faults.begin()
    second, third = cw.raw(path), cw.raw(path)
    assert not faults.reads, (
        f"{path}: a symphony without history rows was treated as a failed read, so the next polls "
        f"ran {len(faults.reads)} shadow_history statements instead of answering the held result"
    )
    assert first == second == third
