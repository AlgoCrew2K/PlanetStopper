"""RED tests for advisors/atlas_cache.py (AC-1..AC-9).

Module under test: advisors.atlas_cache
  - cached_pull(collection_name, fetch_fn, *, ttl_days=7, force_refresh=False)
  - init_atlas_cache()
  - ATLAS_CACHE_DB_PATH env var (default alphabot_atlas_cache.db)

ADVERSARIAL FOCUS:
  - TTL boundary is STRICT: age < ttl_days is fresh; age >= ttl_days is stale (AC-6).
    One second under the boundary must be a HIT; exactly at the boundary must be a MISS.
  - fetch_fn spy asserts exact call counts: hit=0, miss=1, force=1.
  - Never-raises contract (AC-5, AC-7): every exception path inside cached_pull must
    be swallowed; the function must return a value (possibly None) — never propagate.
  - Secret isolation (AC-8, AC-9): MONGO_URI and state/optimization DB imports are
    structurally forbidden in atlas_cache.py.

Clock control strategy: tests pre-seed rows directly into the SQLite DB with a
`fetched_at` value computed from `datetime.utcnow() - timedelta(seconds=age_seconds)`.
This exercises the real production code path for TTL comparison without mocking
any production symbol. A `now_fn` injection point (if the implementer adds one for
testability) may additionally be used, but tests are written to be agnostic to it.

All tests use an isolated temp DB via the `atlas_cache_db` fixture; no test touches
the real alphabot_atlas_cache.db or alphabot_state.db.

No live network calls. No Mongo client. No production DB writes.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Golden fixture path
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TTL_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "math" / "atlas_cache_ttl_boundary.json"
)


def _load_ttl_fixture() -> dict:
    """Load the TTL boundary fixture; skip gracefully if absent."""
    if not _TTL_FIXTURE_PATH.exists():
        pytest.skip(f"TTL fixture missing: {_TTL_FIXTURE_PATH}")
    with _TTL_FIXTURE_PATH.open() as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def atlas_cache_db(tmp_path, monkeypatch):
    """Redirect ATLAS_CACHE_DB_PATH to a per-test temp file.

    Returns the path as a string so individual tests can also connect directly
    to inspect DB state after a cached_pull call.
    """
    db_path = str(tmp_path / "test_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    # Force a fresh module import so the env var is picked up.
    # atlas_cache may read the env at import time or at call time; we cover both
    # by reloading if already imported.
    if "advisors.atlas_cache" in sys.modules:
        importlib.reload(sys.modules["advisors.atlas_cache"])
    yield db_path


def _seed_row(db_path: str, collection: str, payload: object, age_seconds: int) -> None:
    """Insert a pre-aged row directly into the cache DB for clock-controlled tests.

    Uses the real schema expected by init_atlas_cache() so the implementer cannot
    satisfy these tests with a different schema shape.
    """
    fetched_at = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS atlas_cache "
            "(collection TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) "
            "VALUES (?, ?, ?)",
            (collection, fetched_at, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _spy_fetch(return_value: object = None, raises: Exception | None = None):
    """Return a callable spy that records how many times it was called.

    If `raises` is set, the spy raises that exception on call.
    """
    mock = MagicMock(
        side_effect=raises if raises is not None else None,
        return_value=return_value,
    )
    return mock


# ---------------------------------------------------------------------------
# AC-1: Cache HIT — fetch_fn must NOT be called when a fresh row exists
# ---------------------------------------------------------------------------


class TestCacheHit:
    """AC-1: a fresh cached row is returned without calling fetch_fn."""

    def test_cache_hit_does_not_call_fetch_fn(self, atlas_cache_db):
        """HIT: row present and age < ttl_days → fetch_fn call count == 0."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()

        # Seed a row that is 1 second old (well within 7-day TTL).
        stored_payload = {"records": [{"sym": "AAPL", "val": 42}]}
        _seed_row(atlas_cache_db, "community_strats", stored_payload, age_seconds=1)

        fetch = _spy_fetch(return_value={"should": "not be called"})
        result = atlas_cache.cached_pull("community_strats", fetch, ttl_days=7)

        # fetch_fn must NOT have been called at all.
        assert fetch.call_count == 0, (
            f"Expected 0 fetch_fn calls on HIT; got {fetch.call_count}"
        )
        # The returned value must be the cached payload, not the spy return value.
        assert result == stored_payload, (
            f"Expected cached payload {stored_payload!r}; got {result!r}"
        )

    def test_cache_hit_returns_deserialized_payload(self, atlas_cache_db):
        """HIT: the return value is the JSON-deserialized cached payload (not a string)."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        original = {"tickers": ["SPY", "QQQ"], "score": 0.91}
        _seed_row(atlas_cache_db, "frontrunner", original, age_seconds=3600)

        fetch = _spy_fetch()
        result = atlas_cache.cached_pull("frontrunner", fetch, ttl_days=7)

        assert isinstance(result, dict), (
            f"Expected dict from deserialized cache; got {type(result)}"
        )
        assert result == original


# ---------------------------------------------------------------------------
# AC-2: Cache MISS — fetch_fn called exactly once; row upserted
# ---------------------------------------------------------------------------


class TestCacheMiss:
    """AC-2: no row or stale row triggers exactly one fetch_fn call + upsert."""

    def test_cache_miss_no_row_calls_fetch_fn_once(self, atlas_cache_db):
        """MISS (no row): fetch_fn called exactly once."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        live_payload = {"data": "fresh from atlas"}
        fetch = _spy_fetch(return_value=live_payload)

        result = atlas_cache.cached_pull("new_collection", fetch, ttl_days=7)

        assert fetch.call_count == 1, (
            f"Expected exactly 1 fetch_fn call on MISS (no row); got {fetch.call_count}"
        )
        assert result == live_payload

    def test_cache_miss_upserts_row(self, atlas_cache_db):
        """MISS (no row): after fetch, a row is persisted in the DB."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        live_payload = [{"id": 1}, {"id": 2}]
        fetch = _spy_fetch(return_value=live_payload)

        atlas_cache.cached_pull("community_strats", fetch, ttl_days=7)

        # Verify the row now exists in the DB.
        conn = sqlite3.connect(atlas_cache_db)
        try:
            row = conn.execute(
                "SELECT payload FROM atlas_cache WHERE collection=?",
                ("community_strats",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "Expected a row to be upserted after MISS fetch"
        stored = json.loads(row[0])
        assert stored == live_payload, (
            f"Stored payload {stored!r} does not match fetched payload {live_payload!r}"
        )

    def test_cache_miss_stale_row_calls_fetch_fn_once(self, atlas_cache_db):
        """MISS (stale row): age >= ttl_days → fetch_fn called once."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        # Seed a row that is exactly 7 days old (stale per AC-6: >= ttl_days).
        ttl_days = 7
        age_seconds = ttl_days * 86400  # exactly at boundary — stale
        _seed_row(atlas_cache_db, "frontrunner", {"old": "data"}, age_seconds=age_seconds)

        fresh_payload = {"new": "data"}
        fetch = _spy_fetch(return_value=fresh_payload)

        result = atlas_cache.cached_pull("frontrunner", fetch, ttl_days=ttl_days)

        assert fetch.call_count == 1, (
            f"Expected 1 fetch_fn call on stale MISS; got {fetch.call_count}"
        )
        assert result == fresh_payload

    def test_cache_miss_returns_fetched_payload(self, atlas_cache_db):
        """MISS: the return value is exactly what fetch_fn returned."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        payload = {"nested": {"deep": [1, 2, 3]}}
        fetch = _spy_fetch(return_value=payload)

        result = atlas_cache.cached_pull("some_collection", fetch, ttl_days=7)

        assert result == payload


# ---------------------------------------------------------------------------
# AC-3: force_refresh=True — always calls fetch_fn even with a fresh row
# ---------------------------------------------------------------------------


class TestForceRefresh:
    """AC-3: force_refresh=True bypasses the cache unconditionally."""

    def test_force_refresh_calls_fetch_fn_with_fresh_row(self, atlas_cache_db):
        """force_refresh=True on a fresh-cached row still calls fetch_fn once."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        # Fresh row — 60 seconds old, far within 7-day TTL.
        _seed_row(atlas_cache_db, "community_strats", {"old": "cached"}, age_seconds=60)

        new_payload = {"refreshed": True}
        fetch = _spy_fetch(return_value=new_payload)

        result = atlas_cache.cached_pull(
            "community_strats", fetch, ttl_days=7, force_refresh=True
        )

        assert fetch.call_count == 1, (
            f"force_refresh=True must call fetch_fn; got {fetch.call_count} calls"
        )
        assert result == new_payload

    def test_force_refresh_updates_the_cached_row(self, atlas_cache_db):
        """force_refresh=True upserts the row with the freshly fetched payload."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        _seed_row(atlas_cache_db, "frontrunner", {"stale": "v1"}, age_seconds=60)

        new_payload = {"version": "v2"}
        fetch = _spy_fetch(return_value=new_payload)
        atlas_cache.cached_pull("frontrunner", fetch, ttl_days=7, force_refresh=True)

        conn = sqlite3.connect(atlas_cache_db)
        try:
            row = conn.execute(
                "SELECT payload FROM atlas_cache WHERE collection=?", ("frontrunner",)
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        stored = json.loads(row[0])
        assert stored == new_payload, (
            f"Expected DB row to be updated to {new_payload!r}; got {stored!r}"
        )


# ---------------------------------------------------------------------------
# AC-4: Dedicated SQLite DB at ATLAS_CACHE_DB_PATH; init idempotent + WAL
# ---------------------------------------------------------------------------


class TestInitAtlasCache:
    """AC-4: init_atlas_cache() creates the correct schema at the env-specified path."""

    def test_init_creates_db_at_env_path(self, atlas_cache_db):
        """init_atlas_cache() creates the DB file at ATLAS_CACHE_DB_PATH."""
        from advisors import atlas_cache

        assert not pathlib.Path(atlas_cache_db).exists() or True  # may or may not exist yet
        atlas_cache.init_atlas_cache()
        assert pathlib.Path(atlas_cache_db).exists(), (
            f"DB file not created at ATLAS_CACHE_DB_PATH={atlas_cache_db}"
        )

    def test_init_creates_atlas_cache_table(self, atlas_cache_db):
        """init_atlas_cache() creates a table named 'atlas_cache' with correct columns."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        conn = sqlite3.connect(atlas_cache_db)
        try:
            # Table must exist with the three required columns.
            rows = conn.execute("PRAGMA table_info(atlas_cache)").fetchall()
        finally:
            conn.close()

        col_names = {r[1] for r in rows}
        assert "collection" in col_names, "Missing 'collection' column"
        assert "fetched_at" in col_names, "Missing 'fetched_at' column"
        assert "payload" in col_names, "Missing 'payload' column"

    def test_init_is_idempotent(self, atlas_cache_db):
        """Calling init_atlas_cache() twice raises no error and leaves the DB intact."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        atlas_cache.init_atlas_cache()  # must not raise or corrupt

        conn = sqlite3.connect(atlas_cache_db)
        try:
            rows = conn.execute("PRAGMA table_info(atlas_cache)").fetchall()
        finally:
            conn.close()

        assert len(rows) >= 3, "Table should still have >=3 columns after double init"

    def test_init_enables_wal(self, atlas_cache_db):
        """init_atlas_cache() enables WAL journal mode."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        conn = sqlite3.connect(atlas_cache_db)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()

        assert mode == "wal", (
            f"Expected WAL journal mode; got {mode!r}. "
            "WAL is required for concurrent daemon+manual access."
        )


# ---------------------------------------------------------------------------
# AC-5: Never-raises contract — read fail, write fail, any exception
# ---------------------------------------------------------------------------


class TestNeverRaises:
    """AC-5: cached_pull must swallow all internal errors and never propagate."""

    def test_corrupt_row_falls_through_to_live_fetch(self, atlas_cache_db):
        """A corrupt (non-JSON) payload row is treated as a read failure; live fetch is called."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        # Inject a row with a non-JSON payload to simulate corruption.
        conn = sqlite3.connect(atlas_cache_db)
        try:
            fetched_at = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            conn.execute(
                "INSERT INTO atlas_cache (collection, fetched_at, payload) VALUES (?, ?, ?)",
                ("community_strats", fetched_at, "NOT_VALID_JSON{{{{"),
            )
            conn.commit()
        finally:
            conn.close()

        live_payload = {"recovered": "from live"}
        fetch = _spy_fetch(return_value=live_payload)

        # Must NOT raise; must call fetch_fn and return the live value.
        result = atlas_cache.cached_pull("community_strats", fetch, ttl_days=7)

        assert fetch.call_count == 1, (
            f"Expected 1 fetch_fn call after corrupt-row fall-through; got {fetch.call_count}"
        )
        assert result == live_payload

    def test_write_failure_returns_fetched_payload(self, atlas_cache_db, monkeypatch):
        """If the cache write fails, cached_pull returns the live payload without raising."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()

        # Make the DB read-only so any write attempt fails.
        db_path = pathlib.Path(atlas_cache_db)
        db_path.chmod(0o444)
        try:
            live_payload = {"write": "will fail but return this"}
            fetch = _spy_fetch(return_value=live_payload)

            # Must NOT raise.
            result = atlas_cache.cached_pull("some_coll", fetch, ttl_days=7)

            assert result == live_payload, (
                f"Expected live payload on write failure; got {result!r}"
            )
        finally:
            # Restore permissions so temp dir cleanup doesn't fail.
            db_path.chmod(0o644)

    def test_cached_pull_never_raises(self, atlas_cache_db, monkeypatch):
        """cached_pull must not propagate any exception regardless of failure mode."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()

        # Arrange: fetch_fn itself raises an OSError.
        fetch = _spy_fetch(raises=OSError("simulated network error"))

        # Must NOT raise — even if both cache and fetch fail, return sentinel.
        try:
            result = atlas_cache.cached_pull("missing_coll", fetch, ttl_days=7)
        except Exception as exc:
            pytest.fail(
                f"cached_pull raised {type(exc).__name__}: {exc} — violates never-raises contract"
            )

        # Result must be None (or some defined sentinel), never a raw exception.
        assert result is None or isinstance(result, (dict, list)), (
            f"Expected None or data sentinel; got {type(result)}"
        )


# ---------------------------------------------------------------------------
# AC-6: TTL boundary semantics — golden fixture driven
# ---------------------------------------------------------------------------


class TestTTLBoundary:
    """AC-6: boundary is strict: age < ttl_days fresh; age >= ttl_days stale."""

    @pytest.fixture(scope="class")
    def ttl_fixture(self):
        return _load_ttl_fixture()

    def test_ttl_boundary_strictly_less_than_is_fresh(self, atlas_cache_db, ttl_fixture):
        """age < ttl_days (one second under boundary) must be a HIT (fetch_fn not called)."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        # Find the one-second-under-boundary case from fixture.
        case = next(c for c in ttl_fixture["cases"] if c["id"] == "one_second_under_boundary")
        ttl_days = ttl_fixture["ttl_days"]

        stored_payload = {"fresh": "data"}
        _seed_row(atlas_cache_db, "col_fresh", stored_payload, age_seconds=case["age_seconds"])

        fetch = _spy_fetch(return_value={"stale": "should not be returned"})
        result = atlas_cache.cached_pull("col_fresh", fetch, ttl_days=ttl_days)

        assert fetch.call_count == case["expected_fetch_calls"], (
            f"Fixture case '{case['id']}': expected {case['expected_fetch_calls']} fetch calls; "
            f"got {fetch.call_count}"
        )
        # When HIT expected, result must be the stored payload.
        if case["expected_hit"]:
            assert result == stored_payload

    def test_ttl_boundary_exactly_equal_is_stale(self, atlas_cache_db, ttl_fixture):
        """age == ttl_days (exactly at boundary) must be a MISS (fetch_fn called once)."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        case = next(c for c in ttl_fixture["cases"] if c["id"] == "exactly_at_boundary")
        ttl_days = ttl_fixture["ttl_days"]

        _seed_row(atlas_cache_db, "col_stale", {"stale": "data"}, age_seconds=case["age_seconds"])

        fresh_payload = {"fresh": "fetched"}
        fetch = _spy_fetch(return_value=fresh_payload)
        result = atlas_cache.cached_pull("col_stale", fetch, ttl_days=ttl_days)

        assert fetch.call_count == case["expected_fetch_calls"], (
            f"Fixture case '{case['id']}': expected {case['expected_fetch_calls']} fetch calls; "
            f"got {fetch.call_count}"
        )
        if not case["expected_hit"]:
            assert result == fresh_payload

    def test_ttl_env_override_respected(self, atlas_cache_db, monkeypatch):
        """ATLAS_CACHE_TTL_DAYS env var overrides the default ttl_days=7 when no kwarg given."""
        from advisors import atlas_cache

        # Set a 1-day TTL via env.
        monkeypatch.setenv("ATLAS_CACHE_TTL_DAYS", "1")

        atlas_cache.init_atlas_cache()
        # Age is 2 days — stale under 1-day TTL, fresh under 7-day TTL.
        _seed_row(atlas_cache_db, "env_ttl_col", {"old": "data"}, age_seconds=2 * 86400)

        fresh_payload = {"env_ttl": "fresh"}
        fetch = _spy_fetch(return_value=fresh_payload)

        # Call WITHOUT ttl_days kwarg so env-override must apply.
        result = atlas_cache.cached_pull("env_ttl_col", fetch)

        assert fetch.call_count == 1, (
            f"Expected 1 fetch call when env TTL=1 day and row is 2 days old; "
            f"got {fetch.call_count}"
        )
        assert result == fresh_payload


# ---------------------------------------------------------------------------
# AC-7: Stale-on-fetch-failure graceful degradation
# ---------------------------------------------------------------------------


class TestFetchFailureDegradation:
    """AC-7: if fetch_fn raises, return stale row if available; else return None (never raise)."""

    def test_fetch_failure_on_miss_with_stale_row_returns_stale(self, atlas_cache_db):
        """fetch_fn raises on a MISS, but a stale row exists → return stale payload."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        stale_payload = {"stale": "but better than nothing"}
        # Seed a 10-day-old row (stale under default 7-day TTL).
        _seed_row(atlas_cache_db, "degraded_col", stale_payload, age_seconds=10 * 86400)

        fetch = _spy_fetch(raises=RuntimeError("Atlas unreachable"))

        try:
            result = atlas_cache.cached_pull("degraded_col", fetch, ttl_days=7)
        except Exception as exc:
            pytest.fail(
                f"cached_pull raised {type(exc).__name__} instead of returning stale payload"
            )

        assert result == stale_payload, (
            f"Expected stale payload on fetch failure with stale row; got {result!r}"
        )

    def test_fetch_failure_no_row_returns_none_sentinel(self, atlas_cache_db):
        """fetch_fn raises and no row exists → return None (documented sentinel, never raise)."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()
        fetch = _spy_fetch(raises=ConnectionError("Atlas down"))

        try:
            result = atlas_cache.cached_pull("empty_col", fetch, ttl_days=7)
        except Exception as exc:
            pytest.fail(
                f"cached_pull raised {type(exc).__name__}: {exc} — must return None sentinel"
            )

        assert result is None, (
            f"Expected None sentinel when fetch fails and no row exists; got {result!r}"
        )


# ---------------------------------------------------------------------------
# AC-8: Secrets isolation — MONGO_URI never in DB or returned
# ---------------------------------------------------------------------------


class TestSecretsIsolation:
    """AC-8: MONGO_URI must never appear in the cache DB or in any returned payload."""

    def test_mongo_uri_never_stored_in_db(self, atlas_cache_db, monkeypatch):
        """A fetch_fn that returns MONGO_URI-containing data must not persist the URI to DB."""
        # This test asserts the cache stores only what fetch_fn returns.
        # atlas_cache itself must NEVER inject, read, or pass MONGO_URI.
        # The fetch_fn is responsible for projected-docs (no secrets);
        # atlas_cache must not add any env credentials on its own.
        from advisors import atlas_cache

        monkeypatch.setenv("MONGO_URI", "mongodb+srv://secret:password@cluster.mongodb.net/")
        atlas_cache.init_atlas_cache()

        # fetch_fn returns clean projected docs (no URI embedded).
        clean_payload = {"tickers": ["AAPL"], "score": 0.8}
        fetch = _spy_fetch(return_value=clean_payload)
        atlas_cache.cached_pull("clean_col", fetch, ttl_days=7)

        # Walk every byte of the DB file and assert MONGO_URI value is absent.
        mongo_uri = os.environ["MONGO_URI"]
        db_bytes = pathlib.Path(atlas_cache_db).read_bytes()
        assert mongo_uri.encode() not in db_bytes, (
            "MONGO_URI value found in atlas cache DB — credential leak!"
        )

    def test_mongo_uri_never_in_returned_payload(self, atlas_cache_db, monkeypatch):
        """cached_pull must not inject MONGO_URI into the returned value."""
        from advisors import atlas_cache

        monkeypatch.setenv("MONGO_URI", "mongodb+srv://secret:password@cluster.mongodb.net/")
        atlas_cache.init_atlas_cache()

        clean_payload = {"records": [1, 2, 3]}
        _seed_row(atlas_cache_db, "secret_test_col", clean_payload, age_seconds=1)

        fetch = _spy_fetch()
        result = atlas_cache.cached_pull("secret_test_col", fetch, ttl_days=7)

        mongo_uri = os.environ["MONGO_URI"]
        result_str = json.dumps(result, default=str)
        assert mongo_uri not in result_str, (
            f"MONGO_URI found in returned payload: {result_str!r}"
        )


# ---------------------------------------------------------------------------
# AC-9: Structural isolation — no forbidden imports in atlas_cache.py
# ---------------------------------------------------------------------------


class TestStructuralIsolation:
    """AC-9: atlas_cache.py must import neither database.py nor optimization DBs."""

    def test_atlas_cache_imports_no_forbidden_modules(self):
        """atlas_cache.py AST must not import database, autotuner, or pymongo at module level."""
        module_path = _REPO_ROOT / "advisors" / "atlas_cache.py"
        if not module_path.exists():
            pytest.skip("advisors/atlas_cache.py stub not yet written")

        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_prefixes = ("database", "autotuner", "pymongo", "motor", "mongoclient")
        violations = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    # ast.ImportFrom: module attribute may be None for relative imports
                    names = [node.module or ""]
                for name in names:
                    name_lower = name.lower()
                    for forbidden in forbidden_prefixes:
                        if name_lower.startswith(forbidden):
                            violations.append(
                                f"Line {node.lineno}: forbidden import '{name}'"
                            )

        assert not violations, (
            "atlas_cache.py contains forbidden imports (AC-9):\n"
            + "\n".join(violations)
        )

    def test_atlas_cache_collection_primary_key_is_text(self, atlas_cache_db):
        """Schema: 'collection' column is the PRIMARY KEY (one row per collection)."""
        from advisors import atlas_cache

        atlas_cache.init_atlas_cache()

        # Write two rows for the same collection — only the latest must survive.
        payload_v1 = {"v": 1}
        payload_v2 = {"v": 2}
        fetch_v1 = _spy_fetch(return_value=payload_v1)
        fetch_v2 = _spy_fetch(return_value=payload_v2)

        atlas_cache.cached_pull("dup_col", fetch_v1, ttl_days=7)
        atlas_cache.cached_pull("dup_col", fetch_v2, ttl_days=7, force_refresh=True)

        conn = sqlite3.connect(atlas_cache_db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM atlas_cache WHERE collection=?", ("dup_col",)
            ).fetchone()[0]
        finally:
            conn.close()

        assert count == 1, (
            f"Expected exactly 1 row for collection 'dup_col' (PRIMARY KEY upsert); got {count}"
        )
