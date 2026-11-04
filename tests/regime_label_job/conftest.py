"""Harness shared by the regime label job run and logging tests.

Provenance of the data these tests feed the job:
- A state entry is one verbatim symphony entry captured from the droplet
  (tests/fixtures/prod_droplet/bot_state_positions.json, written by the committed
  capture_from_droplet.py); only ``name`` and ``current_holdings`` are replaced, so the entry keeps
  every field the real bot state carries.
- Price closes are derived from the golden return series in tests/fixtures/math/regime_classifier/
  (each file records its own derivation) through ``closes_from_returns``, and served through
  ``AlpacaStub`` in the captured Alpaca response shape (tests/fixtures/alpaca/stock_bars_captured).
  The expected label of a symphony is the ``expected`` field of its golden file, never a value
  copied from a run of the job.
- Symphony ids have the real Composer hash format (20 characters, letters and digits).
"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from collections.abc import Callable

import pytest
import requests

from tests.regime_label_job import _sip

KEY_SENTINEL = "PKSENTINELKEYQ9X2M7"
SECRET_SENTINEL = "SENTINELSECRETW4T8N1ZK6Y3"
DATA_BASE_URL = "https://data.alpaca.markets/v2"

_BOT_STATE_FIXTURE = (
    _sip.REPO_ROOT / "tests" / "fixtures" / "prod_droplet" / ("bot_state_positions.json")
)
_GOLDEN_FILES = {
    "trending": "trending_basic",
    "mean-reverting": "mean_reverting_basic",
    "high-vol": "high_vol_stressed_basic",
}

# Symphony ids in the real Composer hash format.
ID_ONE = "xR4tQ9mZ2bL7vN1cK8pW"
ID_TWO = "hY6sD3fG5jK0qE8wT2uA"
ID_THREE = "pM7nB1vC4xZ9lA3sD6fG"
ID_FOUR = "kJ2hG8dS5aQ1wE9rT4yU"
ID_FIVE = "zX0cV6bN3mL7kJ1hG5fD"
ID_SIX = "qW3eR9tY2uI6oP0aS8dF"
ID_SEVEN = "lK4jH7gF1dS5aP9oI3uY"
ID_EIGHT = "mN8bV2cX6zL0kJ4hG7fD"


def golden_label(kind: str) -> str:
    """The label a golden return series carries in its own fixture file."""
    path = _sip.MATH_DIR / f"{_GOLDEN_FILES[kind]}.json"
    return json.loads(path.read_text(encoding="utf-8"))["expected"]


def golden_closes(kind: str, base: float = 100.0) -> dict:
    """{date: close} over the 21 sessions ending at the newest eligible session, whose 20
    close-to-close returns are the golden series of `kind`."""
    returns = _sip.load_golden_returns(_GOLDEN_FILES[kind])
    sessions = _sip.sessions_ending(_sip.NEWEST_ELIGIBLE, len(returns) + 1)
    return _sip.closes_from_returns(returns, sessions, base=base)


def captured_entry(name: str, holdings: list[dict]) -> dict:
    first = next(iter(json.loads(_BOT_STATE_FIXTURE.read_text(encoding="utf-8")).values()))
    entry = copy.deepcopy(first)
    entry["name"] = name
    entry["current_holdings"] = holdings
    return entry


class JobHarness:
    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.sleeps: list[float] = []
        self.http: _sip.AlpacaStub | None = None
        import synthetic_history  # noqa: PLC0415 -- a conftest imports before DB_PATH is set

        monkeypatch.setattr(synthetic_history, "ALPACA_KEY", KEY_SENTINEL)
        monkeypatch.setattr(synthetic_history, "ALPACA_SECRET", SECRET_SENTINEL)
        monkeypatch.setattr(synthetic_history, "ALPACA_BASE_URL", DATA_BASE_URL)
        monkeypatch.setattr(time, "sleep", lambda seconds: self.sleeps.append(float(seconds)))
        monkeypatch.setattr(requests, "get", self._unserved)

        def _forbidden(*args, **kwargs):
            raise AssertionError("the regime label job must only issue GET requests")

        for verb in ("post", "put", "patch", "delete", "head", "request"):
            monkeypatch.setattr(requests, verb, _forbidden)

    @staticmethod
    def _unserved(*args, **kwargs):
        raise AssertionError("a test reached the network without scripting requests.get")

    def serve(self, closes: dict, rule: Callable | None = None) -> _sip.AlpacaStub:
        """Script requests.get: `closes` is {ticker: {date: close}}; `rule(params)` may return a
        Resp or an exception to make a request fail, or None to serve normally."""
        self.http = _sip.AlpacaStub(closes, rule)
        self.monkeypatch.setattr(requests, "get", self.http)
        return self.http

    def run(self, entries: dict, *, clock: Callable | None = None):
        import regime_label_job  # noqa: PLC0415

        return regime_label_job.run_regime_label_job(
            lambda: entries, clock=clock or (lambda: _sip.JOB_START)
        )

    def _db_path(self) -> str:
        import database  # noqa: PLC0415 -- see __init__

        return database._db_file()

    def rows(self) -> dict[str, tuple[str, str, str]]:
        """{symphony_id: (label, as_of_date, updated_at)} read by SQL from the state database."""
        conn = sqlite3.connect(self._db_path())
        try:
            found = conn.execute(
                "SELECT symphony_id, label, as_of_date, updated_at FROM regime_label_cache"
            ).fetchall()
        finally:
            conn.close()
        return {sid: (label, day, stamp) for sid, label, day, stamp in found}

    def dump_other_tables(self) -> dict[str, list[tuple]]:
        """Every table's rows except regime_label_cache, for before/after comparison."""
        conn = sqlite3.connect(self._db_path())
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                if row[0] != "regime_label_cache"
            ]
            return {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()  # noqa: S608
                for table in tables
            }
        finally:
            conn.close()


@pytest.fixture()
def job_harness(monkeypatch) -> JobHarness:
    return JobHarness(monkeypatch)
