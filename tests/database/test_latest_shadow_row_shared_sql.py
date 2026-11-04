"""The latest ``shadow_history`` row of a symphony and day is selected by one statement.

Two readers pick that row: ``database.load_latest_shadow_row`` (read-write connection, optional
ET cut-off, one ERROR line on failure) and ``analytics._load_latest_shadow_row_for_analytics``
(read-only connection, optionally shared, memoized per request). Their statement text is to be
written once, in a function both call; each reader keeps its own connection, row factory,
cut-off argument and failure behaviour.

* S-4.3 (structural): exactly one function across ``database.py`` and ``analytics.py`` holds the
  statement, and each reader calls it. It fails while the text is written twice.
* S-4.4, S-4.6, S-4.7 (characterization of the base): the database reader on the captured rows
  of ``tests/fixtures/prod_droplet/shadow_*_rows.json``, with and without ``et_cutoff``, on the
  cut-off boundary, and on a missing file, a locked database and a database without the table.

Expectations come from the capture file at run time by the reader's own rule (see
``tests/_shadow_capture_rows.py``); no value of a captured row appears in this file.
"""

from __future__ import annotations

import ast
import logging
import pathlib
import sqlite3

import pytest

import database
from tests import _shadow_capture_rows as cap

REPO = pathlib.Path(__file__).resolve().parents[2]

SQL_MARKERS = (
    "FROM shadow_history",
    "symphony_id = ?",
    "trading_day = ?",
    "ORDER BY ts_utc DESC LIMIT 1",
)


class _OwnScopeStrings(ast.NodeVisitor):
    """The string constants of one scope, not those of the functions nested in it."""

    def __init__(self) -> None:
        self.strings: list[str] = []

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            self.strings.append(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815
    visit_Lambda = visit_FunctionDef  # noqa: N815


def _scope_text(body: list[ast.stmt]) -> str:
    visitor = _OwnScopeStrings()
    for stmt in body:
        visitor.visit(stmt)
    return " ".join(visitor.strings)


def _scopes_holding_the_statement(tree: ast.AST) -> list[str]:
    """Names of the scopes (functions, or '<module>') whose own strings make up the statement."""
    found = []
    if isinstance(tree, ast.Module) and all(m in _scope_text(tree.body) for m in SQL_MARKERS):
        found.append("<module>")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and all(
            m in _scope_text(node.body) for m in SQL_MARKERS
        ):
            found.append(node.name)
    return found


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                names.add(sub.func.id)
            elif isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)
    return names


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    return node


class TestOneStatement:
    def test_the_scan_finds_a_planted_copy(self):
        planted = ast.parse(
            "def a(c):\n"
            "    return c.execute('SELECT * FROM shadow_history WHERE symphony_id = ? '\n"
            "                     'AND trading_day = ? ORDER BY ts_utc DESC LIMIT 1')\n"
            "def b(c, cutoff):\n"
            "    q = 'SELECT * FROM shadow_history WHERE symphony_id = ? AND trading_day = ? '\n"
            "    return c.execute(q + 'ORDER BY ts_utc DESC LIMIT 1')\n"
        )
        assert _scopes_holding_the_statement(planted) == ["a", "b"]

    def test_the_earliest_row_statement_is_not_the_latest_row_statement(self):
        earliest = ast.parse(
            "def first(c):\n"
            "    return c.execute('SELECT * FROM shadow_history WHERE symphony_id = ? "
            "AND trading_day = ? ORDER BY ts_utc ASC LIMIT 1')\n"
        )
        assert _scopes_holding_the_statement(earliest) == []

    def test_exactly_one_function_holds_the_latest_row_statement(self):
        holders = {}
        for name in ("database.py", "analytics.py"):
            tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
            holders[name] = _scopes_holding_the_statement(tree)
        total = sum(len(v) for v in holders.values())
        assert total == 1, (
            f"the latest shadow_history row is selected by {total} statement texts, "
            f"expected one shared definition: {holders}"
        )
        assert "<module>" not in sum(holders.values(), []), "the statement belongs in a function"

    def test_each_reader_calls_the_function_that_holds_it(self):
        trees = {
            name: ast.parse((REPO / name).read_text(encoding="utf-8"))
            for name in ("database.py", "analytics.py")
        }
        holders = [h for tree in trees.values() for h in _scopes_holding_the_statement(tree)]
        assert len(holders) == 1, "precondition: one shared definition (see the test above)"
        shared = holders[0]
        database_reader = _function(trees["database.py"], "load_latest_shadow_row")
        analytics_reader = _function(trees["analytics.py"], "_load_latest_shadow_row_for_analytics")
        for reader in (database_reader, analytics_reader):
            if reader.name == shared:
                continue
            assert shared in _called_names(reader), f"{reader.name} must call {shared}"


class TestDatabaseReaderOnCapturedRows:
    @pytest.mark.parametrize("name", cap.CAPTURE_FILES)
    def test_every_symphony_day_returns_its_latest_row(self, name):
        rows = cap.capture_rows(name)
        cap.seed(rows)
        keyed = cap.groups(rows)
        assert keyed, f"{name} holds no symphony day with a single newest row"

        wrong = []
        for (symphony_id, day), members in keyed.items():
            got = database.load_latest_shadow_row(symphony_id, day)
            if not cap.same_row(got, cap.expected_latest(members)):
                wrong.append((symphony_id, day))
        assert not wrong, f"{len(wrong)} symphony days returned a row other than the latest"

    @pytest.mark.parametrize("name", cap.CAPTURE_FILES)
    def test_a_cutoff_returns_the_latest_row_at_or_before_it(self, name):
        rows = cap.capture_rows(name)
        cap.seed(rows)

        wrong = []
        for (symphony_id, day), members in cap.groups(rows).items():
            cutoff = cap.median_cutoff(members)
            got = database.load_latest_shadow_row(symphony_id, day, et_cutoff=cutoff)
            if not cap.same_row(got, cap.expected_latest(members, cutoff)):
                wrong.append((symphony_id, day))
        assert not wrong, f"{len(wrong)} symphony days returned the wrong row under a cut-off"

    def test_a_cutoff_before_every_row_returns_none(self):
        rows = cap.capture_rows(cap.CAPTURE_FILES[0])
        cap.seed(rows)
        (symphony_id, day), _members = next(iter(cap.groups(rows).items()))

        assert database.load_latest_shadow_row(symphony_id, day, et_cutoff="00:00:00") is None

    def test_no_row_for_the_day_returns_none(self):
        rows = cap.capture_rows(cap.CAPTURE_FILES[0])
        cap.seed(rows)
        (symphony_id, _day), _ = next(iter(cap.groups(rows).items()))

        assert database.load_latest_shadow_row(symphony_id, "1999-01-01") is None
        assert database.load_latest_shadow_row("no-such-symphony", "1999-01-01") is None


class TestCutoffBoundary:
    """S-4.7: the cut-off is inclusive (`<=`) at the second."""

    @staticmethod
    def _derived(template: dict, ts_et_time: str, ts_utc: str) -> dict:
        """A row derived from a captured one with only its ET time of day and its ts_utc set."""
        row = dict(template)
        row["ts_et"] = template["ts_et"][:11] + ts_et_time + template["ts_et"][19:]
        row["ts_utc"] = ts_utc
        return row

    def test_a_row_at_the_cutoff_second_qualifies_and_one_second_after_does_not(self):
        template = cap.capture_rows(cap.CAPTURE_FILES[0])[0]
        at = self._derived(template, "10:30:00", "2026-01-02T15:30:00+00:00")
        after = self._derived(template, "10:30:01", "2026-01-02T15:30:01+00:00")
        at["trading_day"] = after["trading_day"] = "2026-01-02"
        at["symphony_id"] = after["symphony_id"] = "boundary-symphony"

        cap.seed([after])
        assert (
            database.load_latest_shadow_row("boundary-symphony", "2026-01-02", et_cutoff="10:30:00")
            is None
        ), "a row one second after the cut-off must not qualify"

        cap.seed([at])
        got = database.load_latest_shadow_row(
            "boundary-symphony", "2026-01-02", et_cutoff="10:30:00"
        )
        assert got is not None and got["ts_et"][11:19] == "10:30:00", (
            "a row at exactly the cut-off must qualify, and the later row must not replace it"
        )

    def test_without_a_cutoff_the_later_row_wins(self):
        template = cap.capture_rows(cap.CAPTURE_FILES[0])[0]
        at = self._derived(template, "10:30:00", "2026-01-02T15:30:00+00:00")
        after = self._derived(template, "10:30:01", "2026-01-02T15:30:01+00:00")
        at["trading_day"] = after["trading_day"] = "2026-01-02"
        at["symphony_id"] = after["symphony_id"] = "boundary-symphony"
        cap.seed([at, after])

        got = database.load_latest_shadow_row("boundary-symphony", "2026-01-02")

        assert got["ts_et"][11:19] == "10:30:01"


class TestDatabaseReaderFailures:
    """S-4.6: it returns None and logs one ERROR that names itself."""

    @staticmethod
    def _error_lines(caplog) -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.ERROR and "load_latest_shadow_row" in r.getMessage()
        ]

    def test_a_missing_database_file(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "no_such_directory" / "state.db"))

        with caplog.at_level(logging.ERROR):
            got = database.load_latest_shadow_row("any-symphony", "2026-01-02")

        assert got is None
        assert len(self._error_lines(caplog)) == 1

    def test_a_database_without_the_table(self, monkeypatch, tmp_path, caplog):
        path = tmp_path / "no_shadow_table.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        monkeypatch.setenv("DB_PATH", str(path))

        with caplog.at_level(logging.ERROR):
            got = database.load_latest_shadow_row("any-symphony", "2026-01-02")

        assert got is None
        assert len(self._error_lines(caplog)) == 1

    def test_a_locked_database(self, monkeypatch, tmp_path, caplog):
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
        monkeypatch.setenv("DB_PATH", str(path))
        real_connect = sqlite3.connect
        monkeypatch.setattr(
            sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.05})
        )
        try:
            with caplog.at_level(logging.ERROR):
                got = database.load_latest_shadow_row("any-symphony", "2026-01-02")
        finally:
            holder.execute("ROLLBACK")
            holder.close()

        assert got is None
        assert len(self._error_lines(caplog)) == 1
