"""
RED tests -- Strategy Incubation Gate daily tick (AC-3).

`advisors.incubation.run_incubation_tick` does not exist yet; every test in
this file is expected to fail RED until the implementer adds it.

Composer is NEVER called unmocked in tests (structural no-live-API guard) --
every test here patches `advisors.incubation.run_backtest` (the house
convention for this seam, pinned in .claude/tdd-handoff.md "Import style").

Coverage:
  TICK1: a normal tick appends exactly the new trading-day rows for an
    INCUBATING candidate, using the shared SPY call for spy_return_pct.
  TICK2: catch-up-after-gap -- a single tick backfills MULTIPLE missing days
    in one run_backtest call per candidate (no special-casing needed).
  TICK3: a 422 response -> EXPIRED with the pinned reason token.
  TICK4: N consecutive fetch failures (INCUBATION_MAX_FETCH_FAILURES=5) ->
    EXPIRED; fewer than N -> still INCUBATING.
  TICK5: a successful fetch resets the consecutive-failure counter.
  TICK6: SPY fetch failure -> candidate rows still recorded with
    spy_return_pct=None; status stays INCUBATING (never promoted benchmark-blind).
  TICK7: bill-bound -- at most MAX_INCUBATING + 1 Composer calls in one tick.
  TICK8: an empty ledger tick makes zero Composer calls (no incubating
    candidates -> nothing to fetch, not even the SPY call).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import database as db_module
from database import init_db, run_migrations

_SPY_TREE_NAME = "SPY Benchmark"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_incubation_tick.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _tree(marker: str) -> dict:
    """A minimal synthetic tree dict distinguishable by its marker field."""
    return {"id": f"root-{marker}", "step": "root", "marker": marker}


def _admit(candidate_hash: str, marker: str, backtest_mdd_pct: float = 8.0):
    import json

    db_module.register_incubation_candidate(
        candidate_hash=candidate_hash,
        tree_json=json.dumps(_tree(marker)),
        objective="cut_drawdown",
        provenance="built-new",
        backtest_mdd_pct=backtest_mdd_pct,
    )


def _fake_result(daily_returns: dict[str, float] | None = None, error: str | None = None):
    from advisors.composer_backtest_client import BacktestResult

    if error is not None:
        return BacktestResult(stats=None, data_warnings=[], error=error)
    return BacktestResult(
        stats={"sharpe": 0.5}, data_warnings=[], daily_returns=daily_returns or {}, error=None
    )


def _run_backtest_router(responses: dict[str, "callable"]):
    """Build a run_backtest side_effect that dispatches on the tree's 'marker' key,
    or on the SPY-benchmark tree's known name (per .claude/tdd-handoff.md's pinned
    100%-SPY tree construction)."""

    def _router(raw_value, symphony_id: str = "", **kwargs):
        if isinstance(raw_value, dict) and raw_value.get("name") == _SPY_TREE_NAME:
            key = "__spy__"
        else:
            key = raw_value.get("marker") if isinstance(raw_value, dict) else None
        fn = responses.get(key)
        if fn is None:
            pytest.fail(f"Unexpected run_backtest call for key={key!r}, raw_value={raw_value!r}")
        return fn()

    return _router


# ---------------------------------------------------------------------------
# TICK1: normal single-day append
# ---------------------------------------------------------------------------


class TestNormalTick:
    def test_appends_new_trading_days_with_spy_pairing(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        _admit("hash-tick-1", "cand-ok")

        router = _run_backtest_router(
            {
                "cand-ok": lambda: _fake_result(
                    {"2026-01-05": 0.30, "2026-01-06": 0.10}
                ),
                "__spy__": lambda: _fake_result(
                    {"2026-01-05": 0.05, "2026-01-06": 0.02}
                ),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-tick-1")
        assert row["days_observed"] == 2, (
            f"Expected 2 new trading-day rows appended, got {row['days_observed']}."
        )
        assert row["status"] == "INCUBATING", (
            "A 2-day-old candidate must remain INCUBATING (window is 63 days)."
        )


# ---------------------------------------------------------------------------
# TICK2: catch-up after a multi-day gap, one call
# ---------------------------------------------------------------------------


class TestCatchUpAfterGap:
    def test_single_tick_backfills_every_missing_trading_day(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        _admit("hash-gap-1", "cand-gap")
        db_module.append_incubation_day("hash-gap-1", "2026-01-05", 0.10, 0.05)

        call_count = {"cand-gap": 0}

        def _cand_response():
            call_count["cand-gap"] += 1
            return _fake_result(
                {
                    "2026-01-05": 0.10,  # already recorded -- must not duplicate
                    "2026-01-06": 0.20,
                    "2026-01-07": -0.05,
                    "2026-01-08": 0.15,
                    "2026-01-09": 0.05,
                }
            )

        router = _run_backtest_router(
            {
                "cand-gap": _cand_response,
                "__spy__": lambda: _fake_result(
                    {
                        "2026-01-05": 0.05,
                        "2026-01-06": 0.05,
                        "2026-01-07": 0.05,
                        "2026-01-08": 0.05,
                        "2026-01-09": 0.05,
                    }
                ),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        incubation_module.run_incubation_tick()

        assert call_count["cand-gap"] == 1, (
            "A multi-day gap must be backfilled from a SINGLE run_backtest call per "
            f"candidate, not one call per missing day. Got {call_count['cand-gap']} calls."
        )
        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-gap-1")
        assert row["days_observed"] == 5, (
            f"Expected 5 total observed days (1 pre-existing + 4 backfilled), "
            f"got {row['days_observed']}."
        )


# ---------------------------------------------------------------------------
# TICK3: 422 -> EXPIRED
# ---------------------------------------------------------------------------


class TestComposer422Handling:
    def test_422_response_expires_the_candidate(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        _admit("hash-422-1", "cand-422")

        router = _run_backtest_router(
            {
                "cand-422": lambda: _fake_result(
                    error="HTTP 422: node-type-not-supported"
                ),
                "__spy__": lambda: _fake_result({"2026-01-05": 0.05}),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-422-1")
        assert row["status"] == "EXPIRED"
        assert row["status_reason"] == "composer_422_tree_invalid"


# ---------------------------------------------------------------------------
# TICK4-TICK5: consecutive fetch-failure escalation + reset
# ---------------------------------------------------------------------------


class TestFetchFailureEscalation:
    def test_five_consecutive_failures_expires_the_candidate(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        _admit("hash-fail-1", "cand-failing")

        router = _run_backtest_router(
            {
                "cand-failing": lambda: _fake_result(error="HTTP 500: transient upstream error"),
                "__spy__": lambda: _fake_result({"2026-01-05": 0.05}),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        for _ in range(5):
            incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-fail-1")
        assert row["status"] == "EXPIRED"
        assert row["status_reason"] == "fetch_failures_exhausted"

    def test_four_consecutive_failures_stays_incubating(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        _admit("hash-fail-2", "cand-failing-4")

        router = _run_backtest_router(
            {
                "cand-failing-4": lambda: _fake_result(error="HTTP 500: transient upstream error"),
                "__spy__": lambda: _fake_result({"2026-01-05": 0.05}),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        for _ in range(4):
            incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-fail-2")
        assert row["status"] == "INCUBATING", (
            "4 consecutive failures (below the 5-failure threshold) must not expire "
            f"the candidate yet, got status={row['status']!r}."
        )

    def test_success_resets_the_consecutive_failure_counter(self, isolated_db, monkeypatch):
        """TICK5 (adversarial): 4 failures, then 1 success, then 4 more failures must
        NOT expire the candidate (the counter reset on the success) -- only a 5th
        failure AFTER the reset should expire it."""
        import advisors.incubation as incubation_module

        _admit("hash-fail-3", "cand-flaky")

        state = {"mode": "fail"}

        def _flaky_response():
            if state["mode"] == "fail":
                return _fake_result(error="HTTP 500: transient upstream error")
            return _fake_result({"2026-01-05": 0.10})

        router = _run_backtest_router(
            {
                "cand-flaky": _flaky_response,
                "__spy__": lambda: _fake_result({"2026-01-05": 0.05}),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        for _ in range(4):
            incubation_module.run_incubation_tick()

        state["mode"] = "success"
        incubation_module.run_incubation_tick()  # resets the counter

        state["mode"] = "fail"
        for _ in range(4):
            incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-fail-3")
        assert row["status"] == "INCUBATING", (
            "The intervening success must reset the consecutive-failure counter -- "
            f"4 failures after a reset must not expire the candidate, got "
            f"status={row['status']!r}."
        )


# ---------------------------------------------------------------------------
# TICK6: SPY fetch failure -> candidate rows still recorded, never promoted blind
# ---------------------------------------------------------------------------


class TestSpyFetchFailureDegradation:
    def test_candidate_rows_recorded_with_null_spy_when_spy_call_fails(
        self, isolated_db, monkeypatch
    ):
        import advisors.incubation as incubation_module

        _admit("hash-spy-fail-1", "cand-spy-ok")

        router = _run_backtest_router(
            {
                "cand-spy-ok": lambda: _fake_result({"2026-01-05": 0.10, "2026-01-06": 0.20}),
                "__spy__": lambda: _fake_result(error="HTTP 500: SPY fetch failed"),
            }
        )
        monkeypatch.setattr(incubation_module, "run_backtest", router)

        incubation_module.run_incubation_tick()

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-spy-fail-1")
        assert row["days_observed"] == 2, (
            "Candidate rows must still be recorded even when the shared SPY call "
            f"fails, got days_observed={row['days_observed']}."
        )
        assert row["status"] == "INCUBATING", (
            "Status must never be promoted/failed on a tick where SPY was "
            f"unavailable (benchmark-blind), got status={row['status']!r}."
        )


# ---------------------------------------------------------------------------
# TICK7-TICK8: bill-bound Composer call count
# ---------------------------------------------------------------------------


class TestBillBoundCallCount:
    def test_at_most_max_incubating_plus_one_calls_per_tick(self, isolated_db, monkeypatch):
        import advisors.incubation as incubation_module

        monkeypatch.setattr(db_module, "MAX_INCUBATING", 3, raising=False)
        for i in range(3):
            _admit(f"hash-bill-{i}", f"cand-bill-{i}")

        call_count = {"n": 0}

        def _counting_run_backtest(raw_value, symphony_id: str = "", **kwargs):
            call_count["n"] += 1
            return _fake_result({"2026-01-05": 0.10})

        monkeypatch.setattr(incubation_module, "run_backtest", _counting_run_backtest)

        incubation_module.run_incubation_tick()

        assert call_count["n"] <= 4, (
            f"Expected at most MAX_INCUBATING(3) + 1(SPY) = 4 Composer calls in one "
            f"tick, got {call_count['n']}."
        )

    def test_empty_ledger_makes_zero_composer_calls(self, isolated_db, monkeypatch):
        """TICK8: no INCUBATING candidates -> not even the shared SPY call fires
        (nothing to compare it against)."""
        import advisors.incubation as incubation_module

        call_count = {"n": 0}

        def _counting_run_backtest(raw_value, symphony_id: str = "", **kwargs):
            call_count["n"] += 1
            return _fake_result({})

        monkeypatch.setattr(incubation_module, "run_backtest", _counting_run_backtest)

        incubation_module.run_incubation_tick()

        assert call_count["n"] == 0, (
            f"An empty ledger must make zero Composer calls, got {call_count['n']}."
        )
