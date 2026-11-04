"""The analytics latest-shadow-row reader keeps its contract while its statement moves.

``analytics._load_latest_shadow_row_for_analytics(symphony_id, trading_day, db_file, conn=None)``
reads the newest ``shadow_history`` row of a symphony and day. Characterization of the base, so
all of it passes before the statement is shared and has to pass after:

* S-4.4: on the captured rows (``tests/fixtures/prod_droplet/shadow_*_rows.json``) it returns the
  row the reader's rule selects, with every captured key equal;
* S-4.5: with no connection it opens one read-only connection and closes it; with a connection it
  opens none, saves and restores that connection's ``row_factory`` (also when the query raises),
  is made once per connection inside ``request_reads()`` and every time outside it, returns a copy,
  keeps its name and signature so a patch by name still intercepts, and on an error records one
  build failure and returns None;
* S-4.6: a missing file, a locked database and a database without the table each return None and
  record one build failure.

Expectations come from the capture file at run time (``tests/_shadow_capture_rows.py``).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sqlite3

import pytest

import analytics
import database
from tests import _shadow_capture_rows as cap

REPO = pathlib.Path(__file__).resolve().parents[2]
READ = analytics._load_latest_shadow_row_for_analytics


def _seed_first_capture() -> tuple[str, str, dict]:
    rows = cap.capture_rows(cap.CAPTURE_FILES[0])
    cap.seed(rows)
    (symphony_id, day), members = next(iter(cap.groups(rows).items()))
    return symphony_id, day, cap.expected_latest(members)


def _ro_connection() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{database._db_file()}?mode=ro", uri=True, timeout=10.0)


class _FailureLog:
    """Records the build failures a read notes, as the closed-market hold does."""

    def __enter__(self):
        self.failures: list[bool] = []
        self._token = analytics.BUILD_FAILURES.set(self.failures)
        return self.failures

    def __exit__(self, *exc):
        analytics.BUILD_FAILURES.reset(self._token)


class _CountingConnection:
    """A connection that counts the statements run through it; everything else is the real one."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self.real = real
        self.executed = 0

    @property
    def row_factory(self):
        return self.real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.real.row_factory = value

    def execute(self, *args, **kwargs):
        self.executed += 1
        return self.real.execute(*args, **kwargs)


@pytest.fixture
def connect_spy(monkeypatch):
    """Every sqlite3.connect call made while it is active, passed through to the real one."""
    calls: list[tuple[tuple, dict]] = []
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy)
    return calls


class TestOnCapturedRows:
    @pytest.mark.parametrize("name", cap.CAPTURE_FILES)
    def test_every_symphony_day_returns_its_latest_row(self, name):
        rows = cap.capture_rows(name)
        cap.seed(rows)
        keyed = cap.groups(rows)
        assert keyed, f"{name} holds no symphony day with a single newest row"

        wrong = [
            key
            for key, members in keyed.items()
            if not cap.same_row(
                READ(key[0], key[1], database._db_file()), cap.expected_latest(members)
            )
        ]
        assert not wrong, f"{len(wrong)} symphony days returned a row other than the latest"

    def test_it_never_applies_a_time_cutoff(self):
        rows = cap.capture_rows(cap.CAPTURE_FILES[0])
        cap.seed(rows)
        wrong = [
            key
            for key, members in cap.groups(rows).items()
            if READ(key[0], key[1], database._db_file())["ts_utc"]
            != max(m["ts_utc"] for m in members)
        ]
        assert not wrong

    def test_no_row_for_the_day_is_none_and_not_a_failure(self):
        symphony_id, _day, _ = _seed_first_capture()
        with _FailureLog() as failures:
            got = READ(symphony_id, "1999-01-01", database._db_file())
        assert got is None
        assert failures == [], "an absent row is not a build failure"


class TestConnectionHandling:
    def test_with_no_connection_it_opens_one_read_only_connection(self, connect_spy):
        symphony_id, day, expected = _seed_first_capture()
        connect_spy.clear()

        got = READ(symphony_id, day, database._db_file())

        assert cap.same_row(got, expected)
        assert len(connect_spy) == 1, "one connection per call when none is passed"
        (args, kwargs) = connect_spy[0]
        assert "mode=ro" in args[0] and kwargs.get("uri") is True, (
            "the reader must open its own connection read-only (a write through it must fail)"
        )

    def test_with_a_connection_it_opens_none_and_does_not_close_it(self, connect_spy):
        symphony_id, day, expected = _seed_first_capture()
        conn = _ro_connection()
        connect_spy.clear()
        try:
            got = READ(symphony_id, day, database._db_file(), conn)

            assert cap.same_row(got, expected)
            assert connect_spy == [], "a passed connection is used as it is"
            assert conn.execute("SELECT 1").fetchone() == (1,), "the caller's connection stays open"
        finally:
            conn.close()

    def test_the_row_factory_is_restored_after_a_read(self):
        symphony_id, day, _ = _seed_first_capture()
        conn = _ro_connection()
        marker = lambda cursor, row: row  # noqa: E731
        conn.row_factory = marker
        try:
            READ(symphony_id, day, database._db_file(), conn)
            assert conn.row_factory is marker
        finally:
            conn.close()

    def test_the_row_factory_is_restored_when_the_query_raises(self):
        conn = sqlite3.connect(":memory:")  # no shadow_history table: the query raises
        marker = lambda cursor, row: row  # noqa: E731
        conn.row_factory = marker
        try:
            with _FailureLog() as failures:
                got = READ("any-symphony", "2026-01-02", ":memory:", conn)
            assert got is None
            assert failures == [True], "an error records exactly one build failure"
            assert conn.row_factory is marker
        finally:
            conn.close()


class TestMemoPerRequest:
    def test_inside_request_reads_a_second_read_on_the_same_connection_is_not_run(self):
        symphony_id, day, expected = _seed_first_capture()
        conn = _CountingConnection(_ro_connection())
        try:
            with analytics.request_reads():
                first = READ(symphony_id, day, database._db_file(), conn)
                second = READ(symphony_id, day, database._db_file(), conn)
            assert conn.executed == 1, (
                "the repeated read of one connection is answered from the first"
            )
            assert cap.same_row(first, expected) and first == second
        finally:
            conn.real.close()

    def test_outside_request_reads_every_read_is_run(self):
        symphony_id, day, _ = _seed_first_capture()
        conn = _CountingConnection(_ro_connection())
        try:
            READ(symphony_id, day, database._db_file(), conn)
            READ(symphony_id, day, database._db_file(), conn)
            assert conn.executed == 2
        finally:
            conn.real.close()

    def test_a_read_without_a_connection_is_never_remembered(self, connect_spy):
        symphony_id, day, _ = _seed_first_capture()
        connect_spy.clear()
        with analytics.request_reads():
            READ(symphony_id, day, database._db_file())
            READ(symphony_id, day, database._db_file())
        assert len(connect_spy) == 2

    def test_a_different_symphony_day_is_a_different_memo_entry(self):
        rows = cap.capture_rows(cap.CAPTURE_FILES[0])
        cap.seed(rows)
        keys = list(cap.groups(rows))
        assert len(keys) >= 2, "the capture holds two symphony days"
        conn = _CountingConnection(_ro_connection())
        try:
            with analytics.request_reads():
                for symphony_id, day in keys[:2]:
                    READ(symphony_id, day, database._db_file(), conn)
            assert conn.executed == 2
        finally:
            conn.real.close()

    def test_the_result_is_a_copy_the_caller_may_change(self):
        symphony_id, day, expected = _seed_first_capture()
        conn = _CountingConnection(_ro_connection())
        try:
            with analytics.request_reads():
                first = READ(symphony_id, day, database._db_file(), conn)
                first["current_return"] = "changed by the caller"
                second = READ(symphony_id, day, database._db_file(), conn)
            assert cap.same_row(second, expected), "a later read must not see the caller's change"
        finally:
            conn.real.close()


class TestFailures:
    def test_a_missing_database_file(self, tmp_path):
        with _FailureLog() as failures:
            got = READ(
                "any-symphony", "2026-01-02", str(tmp_path / "no_such_directory" / "state.db")
            )
        assert got is None
        assert failures == [True]

    def test_a_database_without_the_table(self, tmp_path):
        path = tmp_path / "no_shadow_table.db"
        setup = sqlite3.connect(path)
        setup.execute("CREATE TABLE unrelated (x INTEGER)")
        setup.commit()
        setup.close()
        with _FailureLog() as failures:
            got = READ("any-symphony", "2026-01-02", str(path))
        assert got is None
        assert failures == [True]

    def test_a_locked_database(self, tmp_path, monkeypatch):
        path = tmp_path / "locked.db"
        setup = sqlite3.connect(path)
        setup.execute(
            "CREATE TABLE shadow_history (id INTEGER PRIMARY KEY, ts_utc TEXT, ts_et TEXT, "
            "trading_day TEXT, symphony_id TEXT)"
        )
        setup.commit()
        setup.close()
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        real_connect = sqlite3.connect
        monkeypatch.setattr(
            sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.05})
        )
        try:
            with _FailureLog() as failures:
                got = READ("any-symphony", "2026-01-02", str(path))
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert got is None
        assert failures == [True]


class TestIdentityOfTheReader:
    def test_name_and_signature_are_unchanged_so_every_patch_by_name_intercepts(self):
        params = inspect.signature(READ).parameters
        assert list(params) == ["symphony_id", "trading_day", "db_file", "conn"]
        assert params["conn"].default is None
        assert all(params[p].default is inspect.Parameter.empty for p in list(params)[:3])

    def test_the_analytics_callers_reach_it_by_its_module_name(self):
        """A patch replaces the module attribute, so each caller must look the name up there."""
        tree = ast.parse((REPO / "analytics.py").read_text(encoding="utf-8"))
        callers = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_load_latest_shadow_row_for_analytics"
        ]
        assert callers, "analytics must still call the reader through its module-level name"
