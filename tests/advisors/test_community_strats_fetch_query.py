"""RED tests — Atlas-fetch slow-query fix for advisors/community_strats.py::_fetch_fn.

THE DEFECT (PM-verified, see .claude/tdd-handoff.md for full diagnosis):
  `_fetch_fn` issues ONE `collection.find({}, _PROJECTION, sort=[("oos_metrics.sharpe",
  DESCENDING)], allow_disk_use=True).limit(_MAX_FETCH_DOCS)` call. `_PROJECTION` includes
  `edn_string` (a multi-KB field per doc) and `oos_metrics.sharpe` is NOT indexed on
  `captplanet.strategies` — so this sorts the full ~11,227-doc corpus, at FULL document
  size, unindexed, on disk. That consistently exceeds the 45s `_ATLAS_FETCH_TIMEOUT_S`
  bound in production, so every live Atlas pull times out and nothing is ever cached.

THE FIX SHAPE (prescribed): a two-step fetch —
  1. A LIGHTWEIGHT query (small per-doc payload: no `edn_string`) carries the server-side
     sort on `oos_metrics.sharpe` DESCENDING + `.limit(_MAX_FETCH_DOCS)` — sorting small
     docs is far cheaper than sorting full ones, even though the field stays unindexed.
  2. A targeted full-document query, filtered by the identities selected in step 1
     (an indexed `_id` lookup — no sort needed), requesting the full `_PROJECTION`.

ADVERSARIAL FOCUS:
  - TestSlowPatternEliminated: the decisive regression guard — NO find() call may ever
    combine a full-document projection with the sharpe sort. This is what FAILS on the
    unfixed code (its single call does exactly that).
  - TestTwoStepQueryStructure: confirms the prescribed two-step shape exists (a lightweight
    sorted selection query + a separate, unsorted, identity-filtered full-doc query).
  - TestTopNSharpeOrderPreserved: the restructure must not change WHICH docs get selected —
    still exactly the top-N by sharpe (verified against an independently-computed expected
    set, not literal output-list order, since MongoDB's `$in` does not itself guarantee
    result order — see the docstring on that class for why this test does not assert a
    strict output ordering).
  - TestBehaviorPreservedD1AndPipeline: never-raising / D-1 contract and the existing
    validate/dedup/sharpe-filter/limit pipeline must survive the restructure unchanged.

FETCH SEAM DESIGN (matches tests/advisors/test_community_strats_timeout.py):
  `_fetch_fn` is a nested def inside `load_community_strategies` — not a module-level
  name, so it cannot be patched directly. Seam:
    Layer 1 — force the live-fetch leg to run (bypass the cache):
        patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn())
    Layer 2 — control what pymongo does:
        patch("pymongo.MongoClient", return_value=<fake client wrapping _FakeCollection>)
  `_FakeCollection` is a small in-memory Mongo double (not a MagicMock) so this file can
  assert on WHICH docs a given find() call actually returns — not just the call shape —
  which is what TestTopNSharpeOrderPreserved needs.

No live network calls. No production DB writes. No @pytest.mark.live.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from advisors import symphony_schema as ss

# ---------------------------------------------------------------------------
# In-memory Mongo double
# ---------------------------------------------------------------------------

_MISSING = object()


def _dotted_get(doc: dict, key: str, default: Any) -> Any:
    """Read `key` from `doc`, supporting one level of dotted nesting (e.g. 'oos_metrics.sharpe')."""
    if "." in key:
        top, _, rest = key.partition(".")
        sub = doc.get(top)
        return sub.get(rest, default) if isinstance(sub, dict) else default
    return doc.get(key, default)


def _dotted_set(dest: dict, key: str, value: Any) -> None:
    """Write `value` into `dest` at `key`, supporting one level of dotted nesting."""
    if "." in key:
        top, _, rest = key.partition(".")
        dest.setdefault(top, {})[rest] = value
    else:
        dest[key] = value


class _FakeCursor:
    """Minimal cursor double: supports .limit(n) chaining and iteration."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def limit(self, n: int) -> "_FakeCursor":
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    """In-memory Mongo collection double with real (not mocked) find/sort/limit/$in
    semantics, so tests can assert on which docs a query actually selects — not just
    the shape of the call. Every find() call is recorded for structural assertions.

    Supported filter shapes: {} (match all) and {'_id': {'$in': [...]}}.
    Supported projection shapes: top-level inclusion (e.g. {'oos_metrics': 1}) and
    one-level dotted inclusion (e.g. {'oos_metrics.sharpe': 1}).
    """

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self.find_calls: list[dict] = []

    def find(self, filter=None, projection=None, *, sort=None, allow_disk_use=None):  # noqa: A002
        filter = filter or {}
        self.find_calls.append(
            {"filter": filter, "projection": projection, "sort": sort, "allow_disk_use": allow_disk_use}
        )
        matched = self._match(filter)
        if sort:
            matched = self._sorted(matched, sort)
        return _FakeCursor(self._projected(matched, projection))

    def _match(self, filt: dict) -> list[dict]:
        if not filt:
            return list(self._docs)
        id_clause = filt.get("_id")
        if isinstance(id_clause, dict) and "$in" in id_clause:
            wanted = set(id_clause["$in"])
            return [d for d in self._docs if d.get("_id") in wanted]
        # Generic exact-match fallback — keeps the double honest if a future
        # implementation filters on a field other than _id.
        return [d for d in self._docs if all(d.get(k) == v for k, v in filt.items())]

    @staticmethod
    def _sorted(docs: list[dict], sort_spec) -> list[dict]:
        for field, direction in reversed(list(sort_spec)):
            docs = sorted(
                docs, key=lambda d, f=field: _dotted_get(d, f, float("-inf")), reverse=(direction < 0)
            )
        return docs

    @staticmethod
    def _projected(docs: list[dict], projection) -> list[dict]:
        if not projection:
            return [dict(d) for d in docs]
        include_id = projection.get("_id", 1) not in (0, False)
        out = []
        for d in docs:
            proj: dict = {}
            if include_id and "_id" in d:
                proj["_id"] = d["_id"]
            for key, want in projection.items():
                if key == "_id" or not want:
                    continue
                value = _dotted_get(d, key, _MISSING)
                if value is not _MISSING:
                    _dotted_set(proj, key, value)
            out.append(proj)
        return out


def _make_fake_mongo_client(collection: _FakeCollection) -> MagicMock:
    """Wrap a _FakeCollection in the client['db']['coll'] / client.db.coll chain
    that _fetch_fn uses, matching the mock-chain idiom in the sibling test files.
    """
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=collection)
    mock_db.strategies = collection

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_client.captplanet = mock_db
    return mock_client


# ---------------------------------------------------------------------------
# Doc fixtures
# ---------------------------------------------------------------------------


def _make_minimal_tree(ticker: str) -> dict:
    """Return a minimal validate_tree==[] tree via symphony_schema constructors."""
    asset = ss.make_asset(ticker)
    weight_node = ss.make_weight_equal([asset])
    return ss.make_root(f"Test-{ticker}", "daily", [weight_node])


def _make_doc(doc_id: str, sid: str, ticker: str, sharpe: float | None) -> dict:
    """Return a full (unprojected) Mongo doc, as _FakeCollection stores it internally."""
    tree = _make_minimal_tree(ticker)
    oos_metrics = {"sharpe": sharpe} if sharpe is not None else {}
    return {
        "_id": doc_id,
        "sid": sid,
        "name": f"Strategy {sid}",
        "edn_string": json.dumps(tree),
        "oos_metrics": oos_metrics,
    }


@pytest.fixture()
def mod():
    """The module under test. No cache-DB isolation needed: every test here bypasses
    atlas_cache.cached_pull entirely (side_effect=lambda col, fn, **kw: fn()), so the
    real sqlite read/write path never executes — matching the seam used by the
    projection/never-raising tests in the sibling test files in this directory.
    """
    from advisors import community_strats

    return community_strats


def _run_live_fetch(mod, collection: _FakeCollection, **kwargs):
    """Bypass the cache and run load_community_strategies() against `collection`."""
    fake_client = _make_fake_mongo_client(collection)
    with (
        patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn()),
        patch("pymongo.MongoClient", return_value=fake_client),
    ):
        return mod.load_community_strategies(**kwargs)


def _has_heavy_field(projection) -> bool:
    """True if a projection dict requests the heavy 'edn_string' field."""
    projection = projection or {}
    return any("edn_string" in str(key) for key, want in projection.items() if want)


def _has_sharpe_sort(sort_spec) -> bool:
    """True if a sort spec sorts on a field naming 'sharpe' (dotted or plain)."""
    sort_spec = sort_spec or []
    return any("sharpe" in str(field) for field, _direction in sort_spec)


# ---------------------------------------------------------------------------
# The decisive regression guard
# ---------------------------------------------------------------------------


class TestSlowPatternEliminated:
    """No find() call may ever combine a full-document projection (requests the heavy
    'edn_string' field) with a server-side sort on the unindexed 'oos_metrics.sharpe'
    field. That combination is precisely what makes the live query take ~50s and blow
    through _ATLAS_FETCH_TIMEOUT_S on every production pull.
    """

    def test_no_find_call_combines_full_projection_with_sharpe_sort(self, mod, monkeypatch):
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        docs = [
            _make_doc("id-1", "sid-1", "SPY", 2.0),
            _make_doc("id-2", "sid-2", "QQQ", 1.0),
            _make_doc("id-3", "sid-3", "TLT", 0.5),
        ]
        collection = _FakeCollection(docs)

        _run_live_fetch(mod, collection)

        assert collection.find_calls, "find() was never called — the fetch path did not run"

        offenders = [
            call
            for call in collection.find_calls
            if _has_heavy_field(call["projection"]) and _has_sharpe_sort(call["sort"])
        ]
        assert not offenders, (
            "A find() call combined a full-document projection (includes 'edn_string') "
            f"with a sort on oos_metrics.sharpe: {offenders!r}. This is the slow, unindexed "
            "disk-sort pattern that causes AtlasFetchTimeout on every production pull — the "
            "sharpe sort must run against a lightweight projection only."
        )


# ---------------------------------------------------------------------------
# Structural confirmation of the prescribed two-step shape
# ---------------------------------------------------------------------------


class TestTwoStepQueryStructure:
    """Confirms the prescribed fix shape: a lightweight, sharpe-sorted selection query
    and a separate, unsorted, identity-filtered full-document query. Assertions are
    keyed off WHAT each call does (sort present? heavy field requested?) rather than
    positional call index, so an implementation is free to choose its exact call count
    as long as the slow pattern (TestSlowPatternEliminated) is actually gone and both
    halves of the two-step contract are present.
    """

    def test_lightweight_sorted_and_full_unsorted_queries_both_occur(self, mod, monkeypatch):
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        docs = [
            _make_doc("id-1", "sid-1", "SPY", 2.0),
            _make_doc("id-2", "sid-2", "QQQ", 1.0),
            _make_doc("id-3", "sid-3", "TLT", 0.5),
        ]
        collection = _FakeCollection(docs)

        _run_live_fetch(mod, collection)

        assert len(collection.find_calls) >= 2, (
            "expected at least 2 find() calls (a lightweight sharpe-sorted selection query "
            f"+ a separate full-doc identity fetch); got {len(collection.find_calls)}. "
            f"calls={collection.find_calls!r}"
        )

        sorted_light_calls = [c for c in collection.find_calls if _has_sharpe_sort(c["sort"])]
        assert sorted_light_calls, (
            "no find() call carries the sharpe sort — the selection step (sort + cap to "
            "_MAX_FETCH_DOCS) is missing entirely"
        )
        for call in sorted_light_calls:
            assert not _has_heavy_field(call["projection"]), (
                "the sharpe-sorted selection query must be lightweight (no 'edn_string'); "
                f"got projection={call['projection']!r}"
            )

        full_doc_calls = [c for c in collection.find_calls if _has_heavy_field(c["projection"])]
        assert full_doc_calls, (
            "no find() call ever requests the full document ('edn_string') — candidates "
            "would have no tree to validate"
        )
        for call in full_doc_calls:
            assert not call["sort"], (
                "the full-document fetch must not carry a sort — it targets identities "
                f"already selected by the lightweight query; got sort={call['sort']!r}"
            )
            assert call["filter"], (
                "the full-document fetch must filter by identity (e.g. "
                "{'_id': {'$in': [...]}}) rather than re-scanning the whole collection; "
                f"got filter={call['filter']!r}"
            )


# ---------------------------------------------------------------------------
# Correctness of the top-N-by-sharpe selection after the restructure
# ---------------------------------------------------------------------------


class TestTopNSharpeOrderPreserved:
    """The restructure must not silently change WHICH docs get selected: the
    server-side sort + cap must still surface exactly the top-N by sharpe.

    This class asserts the SELECTED SET matches an independently-computed top-N
    (sorted by sharpe) rather than asserting the runtime candidate list is itself
    returned in strict descending order. MongoDB's `{'_id': {'$in': [...]}}` query
    (step 2 of the prescribed fix) does not itself guarantee results are returned
    in the order of the `$in` array, so pinning a strict output order here would
    fail a legitimate two-step implementation that (correctly) re-selects by _id
    without re-sorting the second response. Set-membership is the actual contract;
    "correctly ordered" is proven by how the expected set itself is computed below
    (sorted descending by sharpe, then sliced to the cap).
    """

    def test_only_top_n_by_sharpe_are_selected(self, mod, monkeypatch):
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        cap = 3
        monkeypatch.setattr(mod, "_MAX_FETCH_DOCS", cap)

        specs = [
            ("s-1", "TLT", 0.1),
            ("s-2", "SPY", 2.5),
            ("s-3", "QQQ", 1.8),
            ("s-4", "GLD", 0.9),
            ("s-5", "IVV", 3.0),
        ]
        docs = [_make_doc(f"id-{sid}", sid, ticker, sharpe) for sid, ticker, sharpe in specs]
        collection = _FakeCollection(docs)

        result = _run_live_fetch(mod, collection)

        assert result["available"] is True, f"expected available=True; got {result!r}"
        assert result["stats"]["pulled"] == cap, (
            f"stats['pulled'] must equal the fetch cap ({cap}) when more docs exist than "
            f"the cap; got {result['stats']['pulled']}. The selection cap must still be "
            "enforced after the restructure."
        )

        # Independently compute the expected top-N by sharpe (descending), sliced to cap.
        expected_top_n_sids = {
            sid for sid, _t, _s in sorted(specs, key=lambda row: row[2], reverse=True)[:cap]
        }
        pulled_sids = {c["sid"] for c in result["candidates"]}
        assert pulled_sids == expected_top_n_sids, (
            f"expected the top-{cap}-by-sharpe set {expected_top_n_sids!r} to be selected; "
            f"got {pulled_sids!r}. The two lowest-sharpe docs (s-1=0.1, s-4=0.9) must be "
            "excluded by the sort+cap — not an arbitrary slice of the collection."
        )


# ---------------------------------------------------------------------------
# Preserved contracts: never-raising / D-1 + the validate/dedup/filter/limit pipeline
# ---------------------------------------------------------------------------


class TestBehaviorPreservedD1AndPipeline:
    """The restructure must not regress the existing never-raising / D-1 contract, nor
    the validate/dedup/min_oos_sharpe/limit pipeline that runs on whatever docs the
    fetch returns. These assertions are expected to hold both before and after the
    restructure (they are safety nets proving the fix doesn't break what already works),
    unlike TestSlowPatternEliminated / TestTwoStepQueryStructure above, which are the
    tests that actually fail on the unfixed single-query implementation.
    """

    def test_full_doc_query_failure_is_absorbed_never_raises_d1(self, mod, monkeypatch):
        """If the query requesting the full document raises, load_community_strategies
        must still return available=False with reason=type(exc).__name__ — never raise.
        (On the current unfixed code, its single query already requests the full
        document, so this exercises the same absorption path either way.)
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        docs = [_make_doc("id-1", "sid-1", "SPY", 1.5)]

        class _RaisingOnFullDocCollection(_FakeCollection):
            def find(self, filter=None, projection=None, *, sort=None, allow_disk_use=None):  # noqa: A002
                if _has_heavy_field(projection):
                    raise ConnectionError("full-doc fetch failed")
                return super().find(filter, projection, sort=sort, allow_disk_use=allow_disk_use)

        collection = _RaisingOnFullDocCollection(docs)

        try:
            result = _run_live_fetch(mod, collection)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"load_community_strategies raised {type(exc).__name__}: {exc} — "
                "never-raising D-1 contract violated by the restructure"
            )

        assert result["available"] is False, f"expected available=False; got {result!r}"
        assert result.get("reason") == "ConnectionError", (
            f"expected reason='ConnectionError' (type(exc).__name__); got "
            f"{result.get('reason')!r}"
        )

    def test_pipeline_still_validates_dedups_filters_and_limits(self, mod, monkeypatch):
        """End-to-end through the new fetch shape: validate_tree / dedup-by-hash /
        min_oos_sharpe / limit must all still function correctly.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        dup_ticker = "IVV"
        docs = [
            _make_doc("id-good", "sid-good", "SPY", 2.0),
            _make_doc("id-dup-a", "sid-dup-a", dup_ticker, 1.2),  # dedup loser (lower sharpe)
            _make_doc("id-dup-b", "sid-dup-b", dup_ticker, 1.8),  # dedup winner (higher sharpe)
            _make_doc("id-low", "sid-low", "TLT", 0.1),  # below the min_oos_sharpe floor
        ]
        collection = _FakeCollection(docs)

        result = _run_live_fetch(mod, collection, min_oos_sharpe=1.0, limit=10)

        assert result["available"] is True, f"expected available=True; got {result!r}"
        sids = {c["sid"] for c in result["candidates"]}
        assert "sid-good" in sids, "an independent valid candidate must survive the pipeline"
        assert "sid-low" not in sids, (
            "a doc with sharpe below min_oos_sharpe must still be filtered post-restructure"
        )
        assert "sid-dup-a" not in sids and "sid-dup-b" in sids, (
            "dedup must still keep the higher-sharpe duplicate (sid-dup-b, sharpe=1.8) over "
            "the lower one (sid-dup-a, sharpe=1.2) post-restructure"
        )
        for candidate in result["candidates"]:
            errors = ss.validate_tree(candidate["tree"])
            assert errors == [], f"candidate tree must still pass validate_tree: {errors}"


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (see the identical guard + rationale in the sibling files in this directory).
# ---------------------------------------------------------------------------


def test_no_importlib_reload_in_this_test_module():
    """Guard: this test module must not call importlib.reload (memory-leak anti-pattern).

    Detected via AST (a call to an attribute named 'reload'), so a docstring or comment
    mentioning the word does not trip it. importlib.import_module is allowed.
    """
    module_path = pathlib.Path(__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "reload":
                offenders.append(node.lineno)

    assert not offenders, (
        "importlib.reload(...) found in test_community_strats_fetch_query.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. Patch the relevant "
        "seam (atlas_cache.cached_pull / pymongo.MongoClient) directly instead of reloading."
    )
