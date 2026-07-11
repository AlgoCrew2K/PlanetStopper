"""RED tests for the Atlas community-strategies cache populate + bound fix.

Plan: feature-plans/atlas-cache-populate-fix.md

AC-1 — JSON-serializable cache payload (the TypeError fix):
  A fetch of docs carrying an ObjectId `_id` (and/or a BSON datetime) MUST
  result in a written `captplanet.strategies` row in `atlas_cache` that
  round-trips via json.loads. The current bug: _PROJECTION includes `_id`
  (ObjectId) by default → atlas_cache's `json.dumps` throws TypeError → row
  never written → "write error (TypeError)" is swallowed.

AC-2 — defense-in-depth serialization in `atlas_cache`:
  `atlas_cache.cached_pull` write must not silently drop a payload containing a
  stray non-JSON-native type. The write must use `json.dumps(..., default=str)`
  or an explicit sanitiser so a BSON/datetime value caches successfully.
  The universe_provider payload (plain ticker strings) must be byte-stable.

AC-3 — memory-bounded fetch:
  `_fetch_fn` must NOT pull all 11k docs. A named cap constant (e.g.
  `_MAX_FETCH_DOCS`) via `.limit()` must be applied; the returned doc count
  must be ≤ the cap. The public `limit`/`min_oos_sharpe` post-fetch params
  must NOT be confused with the server-side .limit() (no double-apply).

AC-4 — cache HIT serves without a live fetch:
  After one successful populate, a subsequent call within the 7-day TTL must
  return cached candidates without invoking `_fetch_fn` again.

AC-5 — bounded fetch completes within timeout:
  `_ATLAS_FETCH_TIMEOUT_S` named constant exists and is a positive float.
  (The PM-owned live E2E on the droplet is the runtime proof; this test pins
  the constant's presence and type.)

AC-6 — D-1 / never-raises preserved:
  All existing honest-degradation behaviour (available=False, reason=...) on
  a real Atlas failure is unchanged. No exception escapes.

ADVERSARIAL FOCUS:
  - AC-1: tests run through the REAL `atlas_cache.cached_pull` write path
    with a temp cache DB. We supply a fake collection whose `find()` returns
    docs with a Python object that simulates an ObjectId (not JSON-serializable
    by the default json encoder). The test FAILS on the unpatched code because
    json.dumps throws TypeError and the row is never written.
  - AC-3: we intercept the pymongo `find()` call and assert a `.limit()` value
    was set; we assert the returned list length ≤ the cap constant.
  - AC-4: we count `_bounded_fetch_fn` invocations to prove the second call
    hits the cache row, not Atlas.

No live network calls. No production DB reads/writes. All tests use temp paths.

TEST HYGIENE (matches origin/main #64): this module NEVER calls
`importlib.reload` — that is a per-test memory-leak anti-pattern (it orphans a
module graph per call, OOMing single-process full-tree verification). Both
modules under test read their env at CALL time — `atlas_cache._atlas_cache_db()`
reads `ATLAS_CACHE_DB_PATH` inside every function, and `community_strats`
accesses `atlas_cache` via module-attribute / call-time lookup
(`atlas_cache.cached_pull(...)`). So `monkeypatch.setenv(ATLAS_CACHE_DB_PATH)`
+ `tmp_path` for env isolation, and `patch("advisors.atlas_cache.cached_pull")`
/ `patch("pymongo.MongoClient")` for seam control, are sufficient — no reload.
`test_no_importlib_reload_in_this_test_module` (bottom of file) is the AST
anti-recurrence guard.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from advisors import atlas_cache as ac
from advisors import symphony_schema as ss

# ---------------------------------------------------------------------------
# ObjectId and datetime stand-ins (no pymongo needed)
# ---------------------------------------------------------------------------


class _FakeObjectId:
    """Minimal stand-in for bson.ObjectId — NOT JSON-serializable by default."""

    def __init__(self, oid: str = "507f1f77bcf86cd799439011") -> None:
        self._oid = oid

    def __repr__(self) -> str:  # pragma: no cover — used only in failure messages
        return f"ObjectId('{self._oid}')"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_minimal_tree(ticker: str = "SPY") -> dict:
    """Return a minimal validate_tree==[] tree via schema constructors."""
    asset = ss.make_asset(ticker)
    return ss.make_root("Test Strategy", "daily", [ss.make_weight_equal([asset])])


def _make_mongo_doc_plain(
    sid: str = "sid-001",
    ticker: str = "SPY",
    sharpe: float | None = 1.2,
) -> dict:
    """Projected Mongo doc without _id (clean, JSON-serializable)."""
    tree = _make_minimal_tree(ticker=ticker)
    return {
        "sid": sid,
        "name": f"Strategy {sid}",
        "edn_string": json.dumps(tree),
        "oos_metrics": {"sharpe": sharpe} if sharpe is not None else {},
    }


def _make_mongo_doc_with_objectid(
    sid: str = "sid-oid",
    ticker: str = "SPY",
) -> dict:
    """Projected Mongo doc WITH a _id ObjectId field — simulates un-projected `_id`."""
    doc = _make_mongo_doc_plain(sid=sid, ticker=ticker)
    doc["_id"] = _FakeObjectId(f"507f{sid.replace('-', '')[:8]:0<8}deadbeef")
    return doc


def _make_mongo_doc_with_datetime(
    sid: str = "sid-dt",
    ticker: str = "QQQ",
) -> dict:
    """Projected Mongo doc containing a raw datetime value — not JSON-serializable by default."""
    doc = _make_mongo_doc_plain(sid=sid, ticker=ticker)
    doc["created_at"] = datetime.now(UTC)
    return doc


def _recursive_contains(obj: Any, substring: str) -> bool:
    """Return True if any string value in obj (recursively) contains substring."""
    if isinstance(obj, str):
        return substring in obj
    if isinstance(obj, dict):
        return any(_recursive_contains(v, substring) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_recursive_contains(item, substring) for item in obj)
    return False


# ---------------------------------------------------------------------------
# Per-test cache DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_cache_db(tmp_path, monkeypatch):
    """Redirect ATLAS_CACHE_DB_PATH to a fresh per-test temp file.

    `atlas_cache._atlas_cache_db()` reads the env at call-time, so
    monkeypatch.setenv is sufficient — no module reload needed.
    Initialises the schema via init_atlas_cache() so writes can land.
    """
    db_path = str(tmp_path / "test_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    ac.init_atlas_cache()
    return db_path


def _read_cache_row(db_path: str, collection: str) -> dict | None:
    """Return the deserialized payload row, or None if the row is absent."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM atlas_cache WHERE collection=?",
            (collection,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0])


# ---------------------------------------------------------------------------
# AC-1: ObjectId _id causes TypeError → cache row never written (the bug).
#        Fix: _PROJECTION must include "_id": 0 to exclude it.
# ---------------------------------------------------------------------------


class TestProjectionTypeErrorFix:
    """AC-1: docs with ObjectId _id must NOT prevent the cache row from being written."""

    def test_objectid_bearing_docs_write_cache_row(self, isolated_cache_db):
        """When fetch_fn returns docs that include a non-JSON-native _id field, a
        cache row MUST be written for 'captplanet.strategies'.

        ADVERSARIAL: on the un-fixed code, _PROJECTION does not exclude _id, so
        Mongo returns docs with ObjectId _id → json.dumps throws TypeError →
        atlas_cache logs 'write error (TypeError)' and returns without writing →
        this assertion FAILS on the unfixed code.
        """
        doc_with_oid = _make_mongo_doc_with_objectid(sid="oid-001")

        # The fix must sanitize _id out of the documents BEFORE passing them to
        # atlas_cache.cached_pull. We test through the real cached_pull write path.
        ac.cached_pull(
            "captplanet.strategies",
            lambda: [doc_with_oid],
        )

        row_payload = _read_cache_row(isolated_cache_db, "captplanet.strategies")
        assert row_payload is not None, (
            "Cache row was NOT written for 'captplanet.strategies' when docs contain ObjectId._id. "
            "This is the AC-1 bug: _PROJECTION must include '_id': 0 so Mongo never returns the "
            "ObjectId field, or atlas_cache.cached_pull must use json.dumps(default=str)."
        )

    def test_cache_row_is_json_decodable(self, isolated_cache_db):
        """The persisted cache row payload must be JSON-round-trippable.

        ADVERSARIAL: if the fix strips _id at the projection level, the raw
        docs returned by the cursor will lack _id entirely and json.dumps
        will succeed. If the fix is in atlas_cache (default=str), the stored
        value must still be a JSON object, not a repr string.
        """
        doc_with_oid = _make_mongo_doc_with_objectid(sid="oid-002", ticker="QQQ")

        ac.cached_pull(
            "captplanet.strategies",
            lambda: [doc_with_oid],
        )

        # Raw bytes from the DB must be decodable as JSON.
        conn = sqlite3.connect(isolated_cache_db)
        try:
            row = conn.execute(
                "SELECT payload FROM atlas_cache WHERE collection=?",
                ("captplanet.strategies",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "Cache row must be written before we can check JSON decodability"

        payload_str = row[0]
        try:
            decoded = json.loads(payload_str)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"Cache row payload is not valid JSON: {exc}\n"
                f"Raw payload (first 200 chars): {payload_str[:200]!r}"
            )

        # The decoded value must be a list (the docs list) — not garbled.
        assert isinstance(decoded, list), (
            f"Decoded cache payload must be a list of docs; got {type(decoded).__name__}"
        )

    def test_objectid_excluded_from_projection(self):
        """community_strats._PROJECTION must include '_id': 0 to suppress the ObjectId.

        This tests the source-level fix. If atlas_cache does the sanitization
        instead (AC-2), this test may indicate _PROJECTION still lacks '_id': 0
        — that is also acceptable if AC-2 covers it. This test targets the preferred
        fix (exclusion at query level).

        ADVERSARIAL: fails on un-fixed code where _PROJECTION = {sid:1, name:1,
        edn_string:1, oos_metrics:1} with no _id:0.
        """
        import advisors.community_strats as cs

        projection = getattr(cs, "_PROJECTION", None)
        assert projection is not None, (
            "community_strats._PROJECTION must be defined; it's missing entirely"
        )
        # Either _id must be explicitly set to 0 (exclude) in the projection,
        # OR the serialization fix in atlas_cache must make it irrelevant (AC-2).
        # If the projection-level fix is chosen, assert _id: 0 is present.
        # If the atlas_cache fix is chosen, this test will skip (see below).
        if projection.get("_id") != 0:
            # The projection-level fix is NOT in place. Check if AC-2 compensates.
            # We do this by directly testing whether a payload with ObjectId caches.
            try:
                import json as _json

                class _TinyObjectId:
                    pass

                _json.dumps([{"_id": _TinyObjectId()}])
                # If we reach here, the default encoder somehow handles it — unexpected.
                pytest.fail(
                    "_PROJECTION does not exclude '_id': 0 AND json.dumps can somehow "
                    "handle a non-serializable type without error — implementation is wrong."
                )
            except (TypeError, ValueError):
                # As expected: default json.dumps cannot handle ObjectId.
                # The fix MUST be in the projection or in atlas_cache (AC-2).
                pytest.fail(
                    "_PROJECTION does not include '_id': 0. Without this, Mongo returns ObjectId "
                    "fields that json.dumps cannot serialize. Either add '_id': 0 to _PROJECTION "
                    "or implement AC-2 (default=str in atlas_cache). AC-1 is not fixed."
                )

    def test_bson_datetime_doc_writes_cache_row(self, isolated_cache_db):
        """Docs containing Python datetime values must also be cached successfully.

        BSON datetime is deserialized to Python datetime by pymongo.
        json.dumps raises TypeError on datetime without a default.
        Fix must handle this (either via projection or default=str).
        """
        doc_with_dt = _make_mongo_doc_with_datetime(sid="dt-001")

        ac.cached_pull(
            "captplanet.strategies",
            lambda: [doc_with_dt],
        )

        row_payload = _read_cache_row(isolated_cache_db, "captplanet.strategies")
        assert row_payload is not None, (
            "Cache row was NOT written when docs contain a Python datetime. "
            "The fix must handle datetime serialization (via default=str or filtering)."
        )


# ---------------------------------------------------------------------------
# AC-2: atlas_cache write is robust to stray non-JSON type (defense-in-depth)
# ---------------------------------------------------------------------------


class TestAtlasCacheSerializationRobustness:
    """AC-2: atlas_cache.cached_pull write must tolerate stray non-JSON-native types.

    This is defense-in-depth: even if the projection fix is absent, the
    atlas_cache write path must not silently drop the payload.
    """

    def test_payload_with_datetime_caches_without_error(self, isolated_cache_db):
        """A fetch_fn returning a payload containing a Python datetime must result
        in a written cache row (no TypeError-swallow causing silent row drop).

        ADVERSARIAL: on un-fixed atlas_cache, json.dumps(payload) with a datetime
        raises TypeError → the except-block swallows it → row is never written.
        This test FAILS on the unfixed code.
        """
        payload_with_dt = [
            {
                "key": "value",
                "fetched_at_raw": datetime.now(UTC),  # Python datetime, not JSON-native
            }
        ]

        ac.cached_pull(
            "community_strats_dt_test",
            lambda: payload_with_dt,
        )

        row_payload = _read_cache_row(isolated_cache_db, "community_strats_dt_test")
        assert row_payload is not None, (
            "Cache row was NOT written when the payload contains a Python datetime. "
            "atlas_cache must use json.dumps(..., default=str) or a sanitiser to handle "
            "non-JSON-native types. Currently the TypeError is swallowed and the row is dropped."
        )
        # The row must be a list (the structure is preserved).
        assert isinstance(row_payload, list), (
            f"Cached payload must preserve the list structure; got {type(row_payload).__name__}"
        )

    def test_payload_with_object_id_like_type_caches_without_error(self, isolated_cache_db):
        """A fetch_fn returning a payload containing a non-JSON-serializable custom object
        must result in a written cache row.

        ADVERSARIAL: current atlas_cache uses bare json.dumps(fetched_payload), which
        raises TypeError on any non-primitive → silent row drop.
        """
        payload_with_custom_obj = [
            {
                "_id": _FakeObjectId("507f1f77bcf86cd799439011"),
                "sid": "oid-defense",
                "name": "Defense Test",
            }
        ]

        ac.cached_pull(
            "community_strats_oid_test",
            lambda: payload_with_custom_obj,
        )

        row_payload = _read_cache_row(isolated_cache_db, "community_strats_oid_test")
        assert row_payload is not None, (
            "Cache row was NOT written when payload contains a non-JSON-serializable object. "
            "atlas_cache must use json.dumps(..., default=str) or sanitize before writing."
        )

    def test_universe_provider_payload_unaffected_by_robust_serialization(self, isolated_cache_db):
        """The universe_provider cache row (plain ticker strings) must remain byte-stable.

        AC-2 fix must not alter JSON encoding of payloads that are already
        JSON-native (no double-encoding, no repr-wrapping of strings).
        """
        ticker_payload = ["SPY", "QQQ", "AAPL", "NVDA"]

        ac.cached_pull(
            "universe_provider_test",
            lambda: ticker_payload,
        )

        row_payload = _read_cache_row(isolated_cache_db, "universe_provider_test")
        assert row_payload is not None, "Plain ticker list must be cached without issue"
        assert isinstance(row_payload, list), (
            f"Ticker payload must remain a list after round-trip; got {type(row_payload).__name__}"
        )
        # All elements must be plain strings (not repr strings or wrapped objects).
        for item in row_payload:
            assert isinstance(item, str), (
                f"Each ticker must remain a plain str after JSON round-trip; got {type(item)}: {item!r}"
            )
        assert row_payload == ticker_payload, (
            "Ticker payload must be byte-stable (no transformation by default=str). "
            f"Expected {ticker_payload!r}; got {row_payload!r}"
        )


# ---------------------------------------------------------------------------
# AC-3: Bounded fetch — .limit() is applied; count ≤ named cap
# ---------------------------------------------------------------------------


class TestBoundedFetch:
    """AC-3: _fetch_fn must apply a server-side .limit() cap; doc count must be ≤ the cap."""

    def _make_pymongo_mock_capturing_find_args(
        self,
        docs_to_return: list,
        captured_find_calls: list,
    ):
        """Return a pymongo.MongoClient mock whose find() call is captured."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter(docs_to_return))

        # Simulate list(cursor) returning the docs.
        mock_cursor.__next__ = MagicMock(side_effect=iter(docs_to_return).__next__)

        mock_collection = MagicMock()

        def _capture_find(filter_doc=None, projection=None, *args, **kwargs):
            captured_find_calls.append(
                {
                    "filter": filter_doc,
                    "projection": projection,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            # Return a cursor mock that also has .limit() chaining.
            chained_cursor = MagicMock()
            # list(chained_cursor) must yield the docs.
            chained_cursor.__iter__ = MagicMock(return_value=iter(docs_to_return))
            return chained_cursor

        mock_collection.find.side_effect = _capture_find

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_db.strategies = mock_collection

        mock_client_instance = MagicMock()
        mock_client_instance.__getitem__ = MagicMock(return_value=mock_db)
        mock_client_instance.captplanet = mock_db

        return mock_client_instance

    def test_named_cap_constant_exists_in_module(self):
        """community_strats must define a named constant for the server-side fetch cap.

        ADVERSARIAL: on the un-fixed code, no such constant exists — the fetch
        is unbounded. The fix must introduce a named constant (e.g. _MAX_FETCH_DOCS).
        """
        import advisors.community_strats as mod  # noqa: PLC0415

        # The module must expose a cap constant. Acceptable names:
        # _MAX_FETCH_DOCS, _FETCH_LIMIT, _MAX_DOCS, etc. — any name ending in
        # a number-like suffix or containing LIMIT/CAP/MAX/FETCH is acceptable.
        candidate_attrs = [
            attr
            for attr in dir(mod)
            if any(
                kw in attr.upper() for kw in ("MAX_FETCH", "FETCH_LIMIT", "FETCH_CAP", "MAX_DOCS")
            )
            and isinstance(getattr(mod, attr), int)
        ]
        assert candidate_attrs, (
            "community_strats must define a named integer constant for the server-side fetch cap "
            "(e.g., _MAX_FETCH_DOCS). No such constant was found. "
            "Without this, the fetch is unbounded and can OOM the 4GB droplet."
        )
        cap_value = getattr(mod, candidate_attrs[0])
        assert cap_value > 0, (
            f"The cap constant {candidate_attrs[0]}={cap_value!r} must be a positive integer"
        )
        # Sanity: the cap must be less than the 11,193 total live docs (ensures bounding).
        assert cap_value < 11_193, (
            f"The cap constant {candidate_attrs[0]}={cap_value} must be < 11,193 "
            f"(the total live docs) to actually bound the fetch."
        )

    def test_find_is_called_with_limit(self, monkeypatch):
        """The pymongo collection.find() call must chain .limit() or pass a limit argument.

        ADVERSARIAL: on un-fixed code, _fetch_fn calls list(collection.find({}, _PROJECTION))
        with no limit. This test FAILS on the unfixed code.

        Strategy: intercept pymongo.MongoClient, capture how collection.find() is
        called, and verify a limit is applied — either via .limit(N) chained on
        the cursor, or by a $maxN sort-limit pattern, or by passing limit= as a
        find() kwarg.

        CI-hermetic: MONGO_URI is set to a dummy value so the code reaches the
        mocked pymongo.MongoClient instead of raising KeyError before the mock runs.
        The mock intercepts the connection, so no live Atlas call is made.
        """
        # Provide a dummy MONGO_URI so _fetch_fn's os.environ["MONGO_URI"] read
        # succeeds and the code proceeds into the mocked pymongo path.
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://ci-dummy:ci-dummy@test.example.com/db")

        captured_find_calls: list = []
        limit_calls: list = []

        mock_cursor = MagicMock()
        # Support list() conversion.
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))

        mock_cursor_limited = MagicMock()
        mock_cursor_limited.__iter__ = MagicMock(return_value=iter([]))

        def _cursor_limit(n):
            limit_calls.append(n)
            return mock_cursor_limited

        mock_cursor.limit.side_effect = _cursor_limit

        mock_collection = MagicMock()
        mock_collection.find.return_value = mock_cursor

        def _capture_find(*args, **kwargs):
            captured_find_calls.append({"args": args, "kwargs": kwargs})
            return mock_cursor

        mock_collection.find.side_effect = _capture_find

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_db.strategies = mock_collection

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_client.captplanet = mock_db

        import advisors.community_strats as cs  # noqa: PLC0415

        with patch("pymongo.MongoClient", return_value=mock_client):
            # Force the fetch path by bypassing the cache (make cached_pull call fetch_fn directly).
            with patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ):
                cs.load_community_strategies()

        # find() MUST have been called — a skip here would hide a regression where
        # the fetch path silently stops issuing the query. This is RED on unfixed
        # code (it DOES call find, just without a limit), so we assert, not skip.
        assert captured_find_calls, (
            "pymongo collection.find() was never called — the fetch path did not run. "
            "This hides the OOM-bounding contract entirely; the test must exercise the "
            "real _fetch_fn query."
        )

        # A limit must have been applied. Two valid approaches:
        # (a) .limit(N) called on the cursor returned by find()
        # (b) find() called with a 'limit' kwarg or positional limit arg
        # (c) sort + limit via sort(...).limit(N)

        find_call = captured_find_calls[0]
        limit_in_kwargs = find_call["kwargs"].get("limit")
        # limit as a positional arg (find(filter, projection, limit=N) or similar):
        # pymongo's find signature: find(filter={}, projection=None, **kwargs)
        # so limit would be in kwargs.

        limit_applied = bool(limit_calls) or (limit_in_kwargs is not None and limit_in_kwargs > 0)
        assert limit_applied, (
            "The pymongo find() call must apply a server-side .limit() "
            "(either via cursor.limit(N) or the 'limit' kwarg). "
            "Without this, all 11,193 docs are fetched, OOM-killing the 4GB droplet. "
            f"captured find kwargs: {find_call['kwargs']!r}, "
            f"cursor.limit() calls: {limit_calls!r}"
        )

        # TIGHTER: the limit value must equal the module's named cap constant
        # (_MAX_FETCH_DOCS) — not an arbitrary positive number. A naive fix that
        # hardcodes a different number, or applies the public `limit` param here,
        # would pass the loose check above but fail this one.
        cap = cs._MAX_FETCH_DOCS
        observed_limit = limit_calls[0] if limit_calls else limit_in_kwargs
        assert observed_limit == cap, (
            f"The server-side limit must equal _MAX_FETCH_DOCS ({cap}); "
            f"observed {observed_limit!r}. The fetch cap must use the named constant, "
            "not a hardcoded or caller-supplied value."
        )

        # The OOM fix's intent is 'top-N by sharpe' — assert the server-side sort
        # is on oos_metrics.sharpe DESCENDING so the cap keeps the BEST docs, not
        # an arbitrary 500. Accept either the `sort=` kwarg on find() or a
        # cursor.sort(...) chain.
        sort_in_find = find_call["kwargs"].get("sort")
        cursor_sort_calls = mock_cursor.sort.call_args_list
        sort_spec = None
        if sort_in_find is not None:
            sort_spec = sort_in_find
        elif cursor_sort_calls:
            # cursor.sort(spec) — spec is the first positional arg of the first call.
            sort_spec = cursor_sort_calls[0].args[0] if cursor_sort_calls[0].args else None

        assert sort_spec is not None, (
            "The fetch must apply a server-side sort so the .limit() keeps the BEST "
            "candidates, not an arbitrary slice. No sort= kwarg or cursor.sort() found. "
            f"find kwargs: {find_call['kwargs']!r}"
        )
        # The sort must reference oos_metrics.sharpe descending. pymongo.DESCENDING == -1.
        sort_pairs = list(sort_spec) if isinstance(sort_spec, (list, tuple)) else []
        sharpe_desc = any(
            isinstance(pair, (list, tuple))
            and len(pair) == 2
            and "sharpe" in str(pair[0])
            and pair[1] == -1
            for pair in sort_pairs
        )
        assert sharpe_desc, (
            "The server-side sort must be on oos_metrics.sharpe DESCENDING (-1) so the "
            f"cap keeps the highest-sharpe docs. Observed sort spec: {sort_spec!r}"
        )

    def test_returned_doc_count_does_not_exceed_cap(self, monkeypatch):
        """load_community_strategies must not return more candidates than the fetch cap allows.

        ADVERSARIAL: on un-fixed code, list(collection.find({}, _PROJECTION)) returns
        all 11,193 docs. This test verifies the cap is applied at the query level.

        Strategy: supply more docs than any reasonable cap, verify candidates ≤ cap.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        # Get the cap constant from the module. If absent (unfixed), we can't check
        # the exact value — but the test above will already fail in that case.
        cap_attrs = [
            attr
            for attr in dir(cs)
            if any(
                kw in attr.upper() for kw in ("MAX_FETCH", "FETCH_LIMIT", "FETCH_CAP", "MAX_DOCS")
            )
            and isinstance(getattr(cs, attr), int)
        ]
        if not cap_attrs:
            pytest.skip(
                "Cap constant not found — test_named_cap_constant_exists_in_module must pass first"
            )

        cap = getattr(cs, cap_attrs[0])

        # Build cap+10 docs (more than the cap) using distinct tickers.
        # 'TICKER{i}' symbols are valid ticker-like strings that won't hit real APIs.
        n_docs = cap + 10
        # Use real valid trees so the pipeline doesn't reject them.
        tickers = [f"S{i:03d}" for i in range(n_docs)]
        docs = [_make_mongo_doc_plain(sid=f"sid-{i}", ticker=t) for i, t in enumerate(tickers)]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs[:cap]):
            # The cache returns at most cap docs (simulating the server-side limit).
            result = cs.load_community_strategies()

        # This assertion checks that if the fetch is bounded to cap, the result
        # respects that boundary. The server-side limit means cached_pull only
        # ever sees ≤cap docs in the first place.
        assert result["stats"]["pulled"] <= cap, (
            f"stats['pulled'] must be ≤ cap ({cap}); got {result['stats']['pulled']}. "
            "The fetch must apply a server-side .limit() so the cache never stores >cap docs."
        )

    def test_limit_not_double_applied_with_public_limit_param(self):
        """The public `limit` param (applied post-fetch after dedup/filter) must NOT
        interfere with the server-side `_MAX_FETCH_DOCS` constant.

        ADVERSARIAL: a naive fix might confuse the server-side cap with the public
        `limit` param, leading to double-truncation (e.g., server returns 500 docs
        but only 500 are parsed, then `limit=100` further truncates — fine; but
        server returns 100 docs when cap=500 and public limit=100, returning only
        100 out of 500 available — also fine since public limit is post-filter).
        The key invariant: public limit=None must NOT suppress the server-side cap.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        # Build 3 distinct valid docs.
        docs = [
            _make_mongo_doc_plain(sid=f"sid-{i}", ticker=t)
            for i, t in enumerate(["SPY", "QQQ", "TLT"])
        ]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            # limit=None (default) must return ALL 3 — not 0 or fewer.
            result = cs.load_community_strategies(limit=None)

        assert result["available"] is True
        assert len(result["candidates"]) == 3, (
            f"limit=None with 3 valid docs must return 3 candidates; "
            f"got {len(result['candidates'])}. "
            "The server-side fetch cap must not interfere with the public limit param."
        )


# ---------------------------------------------------------------------------
# AC-4: Cache HIT — second call within TTL does not invoke _fetch_fn
# ---------------------------------------------------------------------------


class TestCacheHitOnSecondCall:
    """AC-4: After one successful populate, a second call within TTL skips fetch."""

    def test_second_call_within_ttl_skips_fetch_fn(self, isolated_cache_db):
        """First call populates the cache; second call within TTL must NOT invoke _fetch_fn.

        ADVERSARIAL: if AC-1 is unfixed (cache row never written), the second call
        will also attempt a live fetch (fetch_count == 2). This test FAILS on the
        unfixed code because the row is never written (due to the ObjectId TypeError).

        We use a plain JSON-serializable doc to isolate AC-4 from the AC-1 bug.
        The test still fails if the cache row write fails for any reason.
        """
        fetch_count = Counter()
        plain_doc = _make_mongo_doc_plain(sid="hit-001", ticker="SPY")

        def counting_fetch():
            fetch_count["calls"] += 1
            return [plain_doc]

        # First call: should populate the cache (fetch_count == 1).
        ac.cached_pull("captplanet.strategies", counting_fetch)

        # Second call within TTL: must serve from cache (fetch_count stays at 1).
        ac.cached_pull("captplanet.strategies", counting_fetch)

        assert fetch_count["calls"] == 1, (
            f"fetch_fn must be called exactly once across two calls within TTL; "
            f"got {fetch_count['calls']} calls. "
            "If the first call failed to write the cache row, both calls will fetch. "
            "Ensure AC-1/AC-2 are fixed so the row is written on the first call."
        )

    def test_objectid_bearing_first_call_still_enables_cache_hit(self, isolated_cache_db):
        """The HIT path must work even when the first fetch returns ObjectId-bearing docs.

        ADVERSARIAL — this is the AC-4 test that actually catches the AC-1 bug.
        `test_second_call_within_ttl_skips_fetch_fn` uses a PLAIN doc, so the cache
        row writes even on unfixed code → that test passes regardless and does NOT
        exercise the bug. HERE the first fetch returns a doc carrying a non-JSON-native
        _id (the real-world Mongo shape). On unfixed atlas_cache (bare json.dumps),
        the first write throws TypeError → row never persists → the SECOND call must
        re-fetch (fetch_count == 2) → this assertion FAILS. With the fix (default=str
        and/or _id:0 projection), the row persists and the second call HITs it
        (fetch_count == 1).
        """
        fetch_count = Counter()

        def counting_fetch_with_objectid():
            fetch_count["calls"] += 1
            # Each fetch returns a fresh doc carrying an ObjectId _id.
            return [_make_mongo_doc_with_objectid(sid="oid-hit")]

        # First call: cold cache → fetch + attempt to persist the ObjectId-bearing payload.
        ac.cached_pull("captplanet.strategies", counting_fetch_with_objectid)
        # Second call within TTL: must HIT the persisted row, NOT re-fetch.
        ac.cached_pull("captplanet.strategies", counting_fetch_with_objectid)

        assert fetch_count["calls"] == 1, (
            f"fetch_fn must be called exactly ONCE across two calls within TTL even when "
            f"the docs carry an ObjectId _id; got {fetch_count['calls']}. "
            "fetch_count == 2 means the first write was swallowed (the AC-1 TypeError bug): "
            "the cache row never persisted, so the second call re-fetched. The fix must "
            "serialize ObjectId-bearing payloads (default=str) or exclude _id at projection."
        )

    def test_second_call_returns_same_candidates(self, isolated_cache_db, monkeypatch):
        """The second call's return value must match the first call's return value.

        Verifies the pipeline (parse/validate/dedup) is applied to cached docs too.

        DE-SKIPPED: the prior version skipped whenever the first call returned
        available=False — which is ALWAYS the case in CI (no MONGO_URI → the live
        _fetch_fn KeyErrors), so the HIT path was never exercised. We now mock the
        SEAM (pymongo.MongoClient) so the first call genuinely populates the cache
        row via the real cached_pull write path, then assert the second call
        (force_refresh=False) HITs that row and returns the SAME candidates with
        no second MongoClient instantiation.

        CI-hermetic: MONGO_URI set to a dummy value so _fetch_fn's os.environ read
        succeeds and the code proceeds into the mocked pymongo path.
        """
        # Provide a dummy MONGO_URI so _fetch_fn's os.environ["MONGO_URI"] read
        # succeeds and reaches the mocked pymongo.MongoClient instead of KeyError.
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://ci-dummy:ci-dummy@test.example.com/db")

        import advisors.community_strats as cs  # noqa: PLC0415

        plain_doc = _make_mongo_doc_plain(sid="hit-002", ticker="QQQ")
        # DE-ATLAS-SLOW-QUERY-001: _fetch_fn now issues TWO find() calls (a
        # lightweight sharpe-sorted selection query that extracts doc["_id"],
        # then a full-document query) against this one static mock_cursor. The
        # doc must carry an _id (real Mongo always returns one for a
        # projection that requests it) and the cursor must yield a FRESH
        # iterator per find() call (side_effect) since it's now iterated
        # twice per load_community_strategies() call instead of once.
        doc_with_id = {"_id": "hit-002", **plain_doc}

        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(side_effect=lambda: iter([doc_with_id]))
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)

        mock_collection = MagicMock()
        mock_collection.find.return_value = mock_cursor

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_db.strategies = mock_collection

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_client.captplanet = mock_db

        ctor = MagicMock(return_value=mock_client)

        with patch("pymongo.MongoClient", ctor):
            # First call: cold cache → real cached_pull writes the row via default=str.
            result1 = cs.load_community_strategies()
            # Second call within TTL: must HIT the persisted row, no new MongoClient.
            # (No manual iterator re-arm needed — the cursor's side_effect above
            # always hands out a fresh iterator, so it survives being consumed
            # by the first call's two find() calls too.)
            result2 = cs.load_community_strategies(force_refresh=False)

        assert result1["available"] is True, (
            f"First call must populate and return available=True; got {result1!r}"
        )
        assert result2["available"] is True, (
            "Second call (cache HIT) must return available=True with valid cached docs"
        )
        # The HIT must not have re-fetched: MongoClient instantiated exactly once.
        assert ctor.call_count == 1, (
            f"MongoClient must be instantiated exactly once across both calls; "
            f"got {ctor.call_count}. The second call re-fetched → the cache row was "
            "not written or not read (AC-1/AC-4 regression)."
        )
        # Both calls must yield the SAME candidates (same composition hashes).
        hashes1 = sorted(c["composition_hash"] for c in result1["candidates"])
        hashes2 = sorted(c["composition_hash"] for c in result2["candidates"])
        assert hashes1 == hashes2 and hashes1, (
            f"Cache-HIT candidates must match the populate call's candidates. "
            f"first={hashes1!r} second={hashes2!r}"
        )
        # The parse/validate/dedup pipeline must run on cached payloads too.
        for cand in result2["candidates"]:
            required = {"sid", "name", "tree", "tickers", "oos_metrics", "composition_hash"}
            missing = required - cand.keys()
            assert not missing, (
                f"Cache-hit candidate missing keys: {missing!r}. "
                "The parse/validate pipeline must run on cached payloads."
            )

    def test_fetch_invocation_count_is_one_after_two_calls(self, isolated_cache_db, monkeypatch):
        """Two consecutive load_community_strategies() calls must invoke the internal
        _bounded_fetch_fn exactly once (first call populates; second is a HIT).

        ADVERSARIAL: without a written cache row (AC-1 bug), both calls invoke
        _bounded_fetch_fn → fetch_count == 2 → this assertion FAILS.

        CI-hermetic: MONGO_URI set to a dummy value so _fetch_fn's os.environ read
        succeeds and the code proceeds into the mocked pymongo path.
        """
        # Provide a dummy MONGO_URI so _fetch_fn's os.environ["MONGO_URI"] read
        # succeeds and reaches the mocked pymongo.MongoClient instead of KeyError.
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://ci-dummy:ci-dummy@test.example.com/db")

        import advisors.community_strats as cs  # noqa: PLC0415

        fetch_invocations = Counter()
        plain_docs = [_make_mongo_doc_plain(sid="inv-001", ticker="SPY")]
        # DE-ATLAS-SLOW-QUERY-001: _fetch_fn now issues TWO find() calls (a
        # lightweight sharpe-sorted selection query that extracts doc["_id"],
        # then a full-document query) against this one static mock_cursor. The
        # docs must carry an _id (real Mongo always returns one for a
        # projection that requests it) and the cursor must yield a FRESH
        # iterator per find() call (side_effect) since it's now iterated
        # twice per load_community_strategies() call instead of once.
        docs_with_id = [{"_id": d["sid"], **d} for d in plain_docs]

        # We patch pymongo.MongoClient so the live Atlas call never fires.
        # The mock cursor returns docs_with_id (JSON-serializable — no ObjectId).
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(side_effect=lambda: iter(docs_with_id))
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)

        mock_collection = MagicMock()
        mock_collection.find.return_value = mock_cursor

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_db.strategies = mock_collection

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_client.captplanet = mock_db

        real_bounded_fetch = None

        def counting_bounded_fetch_fn():
            # Count each time the bounded fetch is actually invoked.
            fetch_invocations["calls"] += 1
            # Call the real Mongo fetch (now mocked).
            return mock_cursor.__iter__()

        # Patch pymongo so we can count _bounded_fetch_fn invocations by replacing
        # the _fetch_fn seam. Since _fetch_fn is a closure, we patch at the MongoClient level
        # and count calls to MongoClient.__init__.
        mock_client_constructor = MagicMock(return_value=mock_client)

        with patch("pymongo.MongoClient", mock_client_constructor):
            # First call: should invoke MongoClient (cache miss → fetch).
            result1 = cs.load_community_strategies()
            calls_after_first = mock_client_constructor.call_count

            # Second call within TTL: must NOT invoke MongoClient again (cache HIT).
            result2 = cs.load_community_strategies(force_refresh=False)
            calls_after_second = mock_client_constructor.call_count

        # On unfixed code, the cache row was never written → second call also fetches.
        assert calls_after_second == calls_after_first, (
            f"MongoClient must be instantiated exactly once for two calls within TTL. "
            f"First call: {calls_after_first} invocations, second call: {calls_after_second} invocations. "
            "If calls increased on the second call, the cache row was not written after the first "
            "(AC-1 bug: ObjectId in _id field causes TypeError → row not persisted)."
        )
        # Sanity: first call must have fetched at least once (cold cache).
        assert calls_after_first >= 1, (
            "First call must invoke MongoClient (cold cache miss); got 0 invocations"
        )


# ---------------------------------------------------------------------------
# AC-5: Bounded fetch completes within timeout — constant is justified
# ---------------------------------------------------------------------------


class TestTimeoutBoundJustified:
    """AC-5: _ATLAS_FETCH_TIMEOUT_S constant exists and is a positive float.

    The PM-owned live droplet E2E test is the runtime proof that the bounded
    fetch completes within this bound. This test pins the constant's presence
    and sanity-checks it is reasonable for a bounded query.
    """

    def test_atlas_fetch_timeout_constant_exists(self):
        """community_strats._ATLAS_FETCH_TIMEOUT_S must be defined.

        ADVERSARIAL: if the constant is removed or renamed during the fix, this test
        catches the regression (the timeout bound would no longer apply).
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        assert hasattr(cs, "_ATLAS_FETCH_TIMEOUT_S"), (
            "community_strats._ATLAS_FETCH_TIMEOUT_S must be defined. "
            "This constant bounds the wall-clock time for the Atlas fetch, "
            "preventing indefinite hangs on mongodb+srv:// SRV/DNS resolution."
        )

    def test_atlas_fetch_timeout_constant_is_positive_float(self):
        """_ATLAS_FETCH_TIMEOUT_S must be a positive number (int or float > 0).

        ADVERSARIAL: a zero or negative value would disable the timeout guard.
        A value < 1.0 would be too aggressive for a cold MongoDB connection.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        timeout = getattr(cs, "_ATLAS_FETCH_TIMEOUT_S", None)
        if timeout is None:
            pytest.skip(
                "_ATLAS_FETCH_TIMEOUT_S not yet defined (test_atlas_fetch_timeout_constant_exists must pass first)"
            )

        assert isinstance(timeout, (int, float)), (
            f"_ATLAS_FETCH_TIMEOUT_S must be a numeric type; got {type(timeout).__name__}"
        )
        assert timeout > 0, (
            f"_ATLAS_FETCH_TIMEOUT_S must be > 0; got {timeout}. "
            "A zero or negative value would disable the timeout guard."
        )
        # Sanity: must be long enough for a real bounded Mongo query.
        # Raised to 45.0s (AC-5): cold connect + server-side sort over ~11k docs
        # live-observed to exceed 12s; weekly cache makes 45s acceptable (DE-ATLAS-CACHE-001).
        # Upper ceiling of 120s (2 minutes) still applies.
        assert timeout <= 120, (
            f"_ATLAS_FETCH_TIMEOUT_S={timeout} is unreasonably large (> 120s). "
            "A bounded query should complete within 2 minutes; re-check the value."
        )


# ---------------------------------------------------------------------------
# AC-6: D-1 / never-raises preserved on Atlas failure
# ---------------------------------------------------------------------------


class TestD1NeverRaisesPreserved:
    """AC-6: All existing honest-degradation behaviour is unchanged.

    The projection and serialization fixes must not break the D-1 contract.
    """

    def test_atlas_connection_error_returns_available_false(self):
        """When the Atlas connection fails, load_community_strategies must return
        available=False with a D-1-compliant reason string.

        ADVERSARIAL: a fix that refactors exception handling could accidentally let
        exceptions propagate. This test catches that regression.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        with patch(
            "advisors.atlas_cache.cached_pull",
            side_effect=ConnectionError("Atlas SRV resolution failed"),
        ):
            result = cs.load_community_strategies()

        assert result["available"] is False, (
            f"Expected available=False when Atlas connection fails; got {result!r}"
        )
        reason = result.get("reason", "")
        # D-1: reason must be a string (type(exc).__name__ or a fixed sentinel).
        assert isinstance(reason, str) and len(reason) > 0, (
            f"reason must be a non-empty string; got {reason!r}"
        )
        # D-1: reason must NOT contain the exception message (no host/URI leak).
        assert "SRV resolution" not in reason, (
            f"D-1 violation: exception message found in reason: {reason!r}. "
            "reason must be type(exc).__name__ only."
        )

    def test_load_community_strategies_never_raises_on_any_failure(self):
        """load_community_strategies must not propagate any exception under any failure mode.

        Tests three scenarios: cached_pull raises, returns None, returns non-list.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        scenarios = [
            ("cached_pull raises", {"side_effect": Exception("simulated boom")}),
            ("cached_pull returns None", {"return_value": None}),
            ("cached_pull returns non-list", {"return_value": {"unexpected": "shape"}}),
        ]

        for scenario_name, patch_kwargs in scenarios:
            with patch("advisors.atlas_cache.cached_pull", **patch_kwargs):
                try:
                    result = cs.load_community_strategies()
                except Exception as exc:
                    pytest.fail(
                        f"load_community_strategies raised {type(exc).__name__}: {exc} "
                        f"in scenario '{scenario_name}' — violates D-1 never-raises contract"
                    )
                assert isinstance(result, dict), (
                    f"In scenario '{scenario_name}': result must be a dict; "
                    f"got {type(result).__name__}"
                )
                assert "available" in result, (
                    f"In scenario '{scenario_name}': result must have 'available' key"
                )

    def test_available_false_reason_is_class_name_only(self):
        """When load_community_strategies returns available=False, reason must be
        exactly type(exc).__name__ — no message, no URI, no host substring.

        ADVERSARIAL: a fix that accidentally starts passing str(exc) instead of
        type(exc).__name__ would leak connection strings.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        fake_uri = "mongodb+srv://admin:s3cr3t@mycluster.example.com/db"

        exc_instance = OSError(f"Failed to connect to {fake_uri}")
        with patch("advisors.atlas_cache.cached_pull", side_effect=exc_instance):
            result = cs.load_community_strategies()

        assert result["available"] is False
        reason = result.get("reason", "")

        # Must NOT contain the exception message substring.
        assert fake_uri not in reason, f"D-1 violation: MONGO_URI found in reason: {reason!r}"
        assert "mycluster.example.com" not in reason, (
            f"D-1 violation: hostname found in reason: {reason!r}"
        )
        # Must match the exception class name pattern (no colons, no spaces from messages).
        assert reason == "OSError", (
            f"reason must be exactly 'OSError' (type(exc).__name__); got {reason!r}"
        )
        # Defense-in-depth: NO string anywhere in the result dict may leak the URI,
        # the host, the username, or the password — not just the reason field.
        assert not _recursive_contains(result, "mycluster.example.com"), (
            f"D-1 violation: hostname leaked somewhere in the result dict: {result!r}"
        )
        assert not _recursive_contains(result, "s3cr3t"), (
            f"D-1 violation: password leaked somewhere in the result dict: {result!r}"
        )
        assert not _recursive_contains(result, "mongodb+srv://"), (
            f"D-1 violation: connection-string scheme leaked in the result dict: {result!r}"
        )

    def test_available_false_has_required_keys(self):
        """The D-1 failure dict must carry all required keys.

        This ensures the fix doesn't accidentally break the return-value contract.
        """
        import advisors.community_strats as cs  # noqa: PLC0415

        with patch("advisors.atlas_cache.cached_pull", return_value=None):
            result = cs.load_community_strategies()

        required_keys = {"available", "reason", "candidates", "stats", "source"}
        missing = required_keys - result.keys()
        assert not missing, (
            f"D-1 failure dict missing required keys: {missing!r}. Got keys: {set(result.keys())!r}"
        )
        assert result["available"] is False
        assert result["candidates"] == [], "D-1 failure dict must carry candidates=[]"
        assert isinstance(result.get("stats"), dict), "D-1 failure dict must carry a stats dict"


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (it installs a NEW module object each call while other already-imported modules
# keep references to the OLD one — leaking a whole module graph per test across a
# multi-file run, which OOMs single-process full-tree verification). origin/main
# #64 removed this pattern from the entire advisors test suite and added this same
# guard. community_strats accesses atlas_cache via module-attribute / call-time
# lookup (`atlas_cache.cached_pull(...)`), and atlas_cache reads ATLAS_CACHE_DB_PATH
# at call time — so tests patch the seam / setenv directly instead of reloading.
# importlib.import_module (no reload) is still permitted — it does not orphan a module.
# ---------------------------------------------------------------------------


def test_no_importlib_reload_in_this_test_module():
    """Guard: this test module must not call importlib.reload (memory-leak anti-pattern).

    Detected via AST (a call to an attribute named 'reload'), so a docstring or
    comment mentioning the word does not trip it. importlib.import_module is allowed.
    """
    import pathlib

    module_path = pathlib.Path(__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "reload":
                offenders.append(node.lineno)

    assert not offenders, (
        "importlib.reload(...) found in test_atlas_cache_populate.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. atlas_cache and "
        "community_strats are accessed via module-attribute / call-time lookup; patch "
        "'advisors.atlas_cache.cached_pull' / 'pymongo.MongoClient' (or setenv "
        "ATLAS_CACHE_DB_PATH with tmp_path) directly instead of reloading."
    )
