"""RED tests: Atlas cache cold-miss real-money defect (advisors/atlas_cache.py).

THE DEFECT
----------
`cached_pull()` (advisors/atlas_cache.py:67) never ensures its own schema
exists. Only `init_atlas_cache()` runs `CREATE TABLE IF NOT EXISTS atlas_cache`,
and the only caller that invokes it before `cached_pull()` is
`universe_provider.py:159`. `advisors/community_strats.py:226` -- the caller
that fronts a REAL, PAID `pymongo` Atlas read -- calls `atlas_cache.cached_pull`
directly and never calls `init_atlas_cache()` anywhere in that module.

On a DB where the `atlas_cache` table was never created (a fresh droplet
deploy, a fresh `ATLAS_CACHE_DB_PATH`, a wiped cache file), the SELECT inside
`cached_pull` raises `sqlite3.OperationalError: no such table: atlas_cache`.
That's caught by the broad `except Exception` at atlas_cache.py:128-134 and
silently falls through to a live `fetch_fn()` call. The subsequent INSERT
raises the SAME "no such table" error, caught at atlas_cache.py:192-198, and
is also silently swallowed -- so nothing is ever cached. Every single
`cached_pull()` call on that DB repeats this: live fetch, failed write,
live fetch again. The weekly TTL cache never engages, and every call becomes
a real-money Mongo Atlas pull.

These tests exercise `cached_pull()` on a DB that was NEVER touched by
`init_atlas_cache()` -- reproducing the exact community_strats.py call
pattern -- and must fail against the current implementation.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Fixture: a cache DB path that has NEVER been touched by init_atlas_cache()
# ---------------------------------------------------------------------------


@pytest.fixture()
def cold_atlas_cache_db(tmp_path, monkeypatch):
    """Point ATLAS_CACHE_DB_PATH at a virgin path -- no init_atlas_cache() call,
    no pre-touched file. This is the real-world condition every caller that
    skips init_atlas_cache() hits (community_strats.py:226 is one; any fresh
    droplet deploy before the weekly universe_provider cache warms is another).
    """
    db_path = str(tmp_path / "cold_start_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    assert not pathlib.Path(db_path).exists(), "fixture setup: DB file must not pre-exist"
    yield db_path


# ---------------------------------------------------------------------------
# Decisive real-money regression tests
# ---------------------------------------------------------------------------


class TestColdMissRealMoneyBug:
    """cached_pull() must be self-sufficient: it cannot rely on a caller
    having invoked init_atlas_cache() first, because at least one production
    caller (community_strats.py) never does.
    """

    def test_second_call_within_ttl_is_served_from_cache_not_refetched(
        self, cold_atlas_cache_db
    ):
        """DECISIVE: two cached_pull() calls, same key, same TTL window, on a
        DB that was never init'd. fetch_fn (stand-in for a paid Mongo pull)
        must fire exactly ONCE -- the second call must be a cache HIT.
        """
        from advisors import atlas_cache

        call_count = {"n": 0}

        def fetch_fn():
            call_count["n"] += 1
            return {"docs": [{"symphony": "abc"}], "call": call_count["n"]}

        first = atlas_cache.cached_pull("community_strats", fetch_fn, ttl_days=7)
        second = atlas_cache.cached_pull("community_strats", fetch_fn, ttl_days=7)

        assert call_count["n"] == 1, (
            f"Real-money bug: fetch_fn was called {call_count['n']} times across "
            "2 cached_pull() calls on a never-init'd DB (expected exactly 1). "
            "cached_pull must ensure its own schema exists -- it cannot rely on "
            "callers to invoke init_atlas_cache() first (community_strats.py "
            "never does)."
        )
        assert first == second, (
            "Second call must return the CACHED first-call payload, not a "
            "freshly re-fetched one."
        )

    def test_first_call_creates_table_and_persists_row_without_prior_init(
        self, cold_atlas_cache_db
    ):
        """After ONE cached_pull() call on a never-init'd DB, the atlas_cache
        table must exist and hold a row -- proving cached_pull is
        self-sufficient rather than depending on a prior init_atlas_cache().
        """
        from advisors import atlas_cache

        payload = {"docs": [{"symphony": "xyz"}]}
        fetch = MagicMock(return_value=payload)

        atlas_cache.cached_pull("frontrunner", fetch, ttl_days=7)

        conn = sqlite3.connect(cold_atlas_cache_db)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "atlas_cache" in tables, (
                "atlas_cache table was never created -- cached_pull() must create "
                "its own schema (CREATE TABLE IF NOT EXISTS) rather than assuming "
                "init_atlas_cache() was already called."
            )
            row = conn.execute(
                "SELECT payload FROM atlas_cache WHERE collection=?",
                ("frontrunner",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, (
            "No row persisted after the first cached_pull() call on a never-init'd "
            "DB -- the write silently failed."
        )
        assert json.loads(row[0]) == payload

    def test_ten_calls_on_virgin_db_only_fetch_once(self, cold_atlas_cache_db):
        """Stress the real-money angle: 10 back-to-back calls (a chatty caller
        or a dashboard polling loop hitting a fresh droplet) must still only
        fetch ONCE, not 10 times.
        """
        from advisors import atlas_cache

        call_count = {"n": 0}

        def fetch_fn():
            call_count["n"] += 1
            return {"n": call_count["n"]}

        for _ in range(10):
            atlas_cache.cached_pull("community_strats", fetch_fn, ttl_days=7)

        assert call_count["n"] == 1, (
            f"Expected exactly 1 live fetch across 10 calls on a virgin DB; got "
            f"{call_count['n']} -- each extra call is a real paid Mongo request "
            "that should have been served from cache."
        )
