"""Tests for the session-autouse live-Mongo/Atlas guard
(tests/conftest.py::_no_live_mongo_atlas_connections).

BACKGROUND (real-money-critical, operator escalation 2026-07-11): a test
forgetting to mock advisors.community_strats.load_community_strategies fell
through to a REAL pymongo.MongoClient(os.environ["MONGO_URI"]) construction —
a genuine, billed Atlas connection attempt, 45 times in one discovered case
(test_fable_calls_per_symphony_are_bounded_by_a_budget_cap, pre-fix). The
conftest.py guard mirrors database._db_file()'s pytest sentinel pattern
(database.py:55-74) for the Atlas/Mongo read path: pymongo.MongoClient is
patched session-wide to raise loud instead of connecting.

These tests prove the guard itself works — not the frontrunner feature.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


def test_constructing_a_real_mongo_client_under_pytest_raises_loud():
    """The core guarantee: pymongo.MongoClient(...) — called directly, with
    no test-level mock — must raise immediately under pytest, never attempt
    a real connection."""
    import pymongo

    with pytest.raises(RuntimeError, match="pymongo.MongoClient"):
        pymongo.MongoClient("mongodb://fake-uri-never-actually-dialed")


def test_the_guard_message_names_the_real_money_risk_and_the_fix():
    """The error message must be actionable — name what happened (a real
    Atlas connection attempt) and how to fix it (mock the loader), mirroring
    database._db_file()'s actionable-message convention."""
    import pymongo

    with pytest.raises(RuntimeError) as exc_info:
        pymongo.MongoClient("mongodb://fake-uri-never-actually-dialed")

    message = str(exc_info.value)
    assert "billed" in message.lower() or "real" in message.lower(), (
        f"guard message doesn't convey the stakes (billed Atlas call): {message!r}"
    )
    assert "load_community_strategies" in message, (
        f"guard message doesn't point to the fix (mock load_community_strategies): "
        f"{message!r}"
    )


def test_an_unmocked_community_strats_fetch_fails_fast_not_slow(monkeypatch):
    """End-to-end proof (the actual regression this guard prevents): calling
    advisors.community_strats.load_community_strategies with NOTHING mocked
    and force_refresh=True (bypassing the weekly cache, so the real fetch
    path is genuinely exercised) must resolve almost instantly — proving no
    real network connection/timeout occurs — and degrade honestly to
    available=False, rather than hanging for the ~10s MongoClient
    serverSelectionTimeoutMS or the 12s _ATLAS_FETCH_TIMEOUT_S bound that
    caused the original incident.

    MONGO_URI is set explicitly (monkeypatch) rather than relying on ambient
    .env state, so this test's guard-reachability doesn't depend on whether
    the dev machine happens to have a real credential configured.
    """
    monkeypatch.setenv("MONGO_URI", "mongodb://fake-uri-never-actually-dialed")

    from advisors import community_strats

    start = time.monotonic()
    result = community_strats.load_community_strategies(force_refresh=True)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"load_community_strategies took {elapsed:.2f}s with an unmocked "
        f"pymongo.MongoClient — the guard should make this fail in "
        f"milliseconds (RuntimeError at construction), not hang toward a "
        f"real network timeout. This is the exact regression class the "
        f"guard exists to prevent."
    )
    assert result["available"] is False, (
        f"expected an honest available=False degradation when the (guarded, "
        f"blocked) fetch fails, got {result!r}"
    )


def test_a_tests_own_pymongo_mock_still_overrides_the_guard():
    """Composability proof: many existing tests (test_community_strats*.py,
    test_atlas_cache_populate.py) already do their own
    patch("pymongo.MongoClient", ...) to inject a fake client. The
    session-level guard must NOT block those — a test's own explicit patch
    takes precedence for its duration (standard unittest.mock nesting:
    the innermost active patch wins), reverting to the guard's sentinel
    afterward, never breaking existing per-test Mongo mocking conventions."""
    import pymongo

    sentinel_client = object()

    with patch("pymongo.MongoClient", return_value=sentinel_client):
        # The test's own mock is active — must NOT raise.
        result = pymongo.MongoClient("mongodb://irrelevant")
        assert result is sentinel_client

    # After the test's own patch context exits, the session guard's sentinel
    # is back in effect (never the true unmocked pymongo.MongoClient).
    with pytest.raises(RuntimeError, match="pymongo.MongoClient"):
        pymongo.MongoClient("mongodb://fake-uri-never-actually-dialed")
