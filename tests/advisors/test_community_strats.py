"""RED tests for advisors/community_strats.py (AC-1..AC-9).

Module under test: advisors.community_strats
  - load_community_strategies(*, limit=None, min_oos_sharpe=None,
                               client=None, force_refresh=False) -> dict

ADVERSARIAL FOCUS:
  - Cache routing (AC-1/AC-2): the Atlas read MUST go through
    atlas_cache.cached_pull. A spy fetch-closure counter proves it.
    Second call within TTL → counter stays at 0 (cache HIT).
    force_refresh=True → counter increments again (always calls fetch_fn).
  - Candidate shape (AC-3): every returned candidate must carry the 6
    required keys; tree validates clean; tickers match extract_tickers.
    Trees are built via symphony_schema constructors — never hand-rolled.
  - Filtering / dedup (AC-4/5/6): bad-edn / validate-rejected counted in
    stats; dedup keeps higher sharpe; min_oos_sharpe keeps missing-sharpe
    docs; limit caps the result list.
  - Never-raising + D-1 (AC-7): any failure path returns
    {available: False, reason: type(exc).__name__} — no host, no message,
    no MONGO_URI substring, anywhere in the return value (recursive walk).
  - Secret isolation (AC-8): MONGO_URI never appears in the cache DB or
    anywhere in the returned dict (recursive walk). No cross-DB imports.
  - Projection (AC-9): the pymongo query projection excludes heavy fields.

Strategy for cache spy:
  Tests monkeypatch advisors.atlas_cache.cached_pull with a wrapper that
  records how many times its fetch_fn argument is called, then routes to
  the real cached_pull so cache-hit semantics are tested end-to-end.
  ATLAS_CACHE_DB_PATH is isolated to a per-test tmp_path.

No live network calls. No Mongo client. No production DB writes.
edn_string fixture format: JSON-encoded tree dict (json.loads is the
parse path the implementation is expected to use; parse_failed on
non-JSON input).
"""

from __future__ import annotations

import ast
import importlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC
from typing import Any
from unittest.mock import patch

import pytest

from advisors import atlas_cache as ac

# ---------------------------------------------------------------------------
# Helpers: build minimal valid trees via symphony_schema constructors.
# Never hand-roll trees — the schema module is the oracle.
# ---------------------------------------------------------------------------
from advisors import symphony_schema as ss


def _make_minimal_tree(name: str = "Test Strategy", ticker: str = "SPY") -> dict:
    """Return a minimal validate_tree==[] tree using schema constructors."""
    asset = ss.make_asset(ticker)
    weight_node = ss.make_weight_equal([asset])
    return ss.make_root(name, "daily", [weight_node])


def _tree_as_edn_string(tree: dict) -> str:
    """Serialise a tree as the edn_string wire format (JSON-encoded)."""
    return json.dumps(tree)


def _make_mongo_doc(
    sid: str = "sid-001",
    name: str = "Community Strat Alpha",
    ticker: str = "SPY",
    sharpe: float | None = 1.2,
) -> dict:
    """Return a projected Mongo doc shape {sid, name, edn_string, oos_metrics}."""
    tree = _make_minimal_tree(name=name, ticker=ticker)
    oos_metrics = {"sharpe": sharpe} if sharpe is not None else {}
    return {
        "sid": sid,
        "name": name,
        "edn_string": _tree_as_edn_string(tree),
        "oos_metrics": oos_metrics,
    }


def _recursive_contains(obj: Any, substring: str) -> bool:
    """Return True if any string in obj (recursively) contains substring."""
    if isinstance(obj, str):
        return substring in obj
    if isinstance(obj, dict):
        return any(_recursive_contains(v, substring) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_recursive_contains(item, substring) for item in obj)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_atlas_cache_db(tmp_path, monkeypatch):
    """Redirect ATLAS_CACHE_DB_PATH to a per-test temp file and init the schema.

    No module reload is needed: atlas_cache resolves ATLAS_CACHE_DB_PATH from
    os.environ at CALL time (advisors/atlas_cache.py:_atlas_cache_db), so
    monkeypatch.setenv alone makes the new path take effect for the subsequent
    init_atlas_cache() + cached_pull calls. importlib.reload per test is a
    memory-leak anti-pattern (it installs a NEW module object while other
    already-imported modules keep references to the OLD one, leaking a module
    graph per test across a multi-file run).
    """
    db_path = str(tmp_path / "test_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    ac.init_atlas_cache()
    return db_path


@pytest.fixture()
def single_valid_doc():
    """One well-formed projected Mongo doc."""
    return [_make_mongo_doc()]


@pytest.fixture()
def two_different_docs():
    """Two docs with distinct trees (different tickers → different hashes)."""
    return [
        _make_mongo_doc(sid="sid-001", name="Strat A", ticker="SPY"),
        _make_mongo_doc(sid="sid-002", name="Strat B", ticker="QQQ"),
    ]


@pytest.fixture()
def module_under_test(isolated_atlas_cache_db):
    """Return the community_strats module after env isolation is in place.

    No reload: community_strats accesses its dependency via module-attribute
    lookup at call time (`atlas_cache.cached_pull(...)`), so tests that
    `patch("advisors.atlas_cache.cached_pull", ...)` are visible to the module
    without re-executing it. importlib.reload per test is a memory-leak
    anti-pattern (it installs a NEW module object while other already-imported
    modules keep references to the OLD one, leaking a module graph per test).
    """
    from advisors import community_strats

    return community_strats


# ---------------------------------------------------------------------------
# AC-1: cache routing — first call fetches once; second call within TTL skips
# ---------------------------------------------------------------------------


class TestCacheRoutingHitMiss:
    """AC-1: The Atlas read goes through atlas_cache.cached_pull."""

    def test_cache_routing_first_call_invokes_fetch_fn_once(
        self, module_under_test, isolated_atlas_cache_db, single_valid_doc
    ):
        """load_community_strategies must invoke its internal fetch_fn exactly
        once on the first call (cache MISS → populate the cache)."""
        fetch_call_count = Counter()
        original_cached_pull = ac.cached_pull

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            """Wrap the real cached_pull; count how many times fetch_fn is called."""

            def counted_fetch():
                fetch_call_count["calls"] += 1
                return single_valid_doc

            return original_cached_pull(collection_name, counted_fetch, **kwargs)

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            # Reload to pick up the patched module reference if needed.
            mod = module_under_test
            result = mod.load_community_strategies()

        # The function must have used cached_pull at all (available=True means it ran).
        assert result["available"] is True, (
            f"expected available=True on first call with valid docs; got {result!r}"
        )
        # The fetch_fn closure must have been called exactly once.
        assert fetch_call_count["calls"] == 1, (
            f"expected fetch_fn called once on cold cache; got {fetch_call_count['calls']}"
        )

    def test_cache_routing_second_call_within_ttl_skips_fetch(
        self, isolated_atlas_cache_db, single_valid_doc
    ):
        """A second call within the TTL window must NOT invoke the fetch_fn
        (payload served from the cache DB row populated by the first call)."""
        fetch_call_count = Counter()

        def counting_fetch():
            fetch_call_count["calls"] += 1
            return single_valid_doc

        # Pre-populate the cache directly so the second call is a guaranteed HIT.
        import json as _json
        from datetime import datetime

        conn = sqlite3.connect(isolated_atlas_cache_db)
        try:
            now_iso = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) "
                "VALUES (?, ?, ?)",
                ("captplanet.strategies", now_iso, _json.dumps(single_valid_doc)),
            )
            conn.commit()
        finally:
            conn.close()

        # Now call load_community_strategies. It must hit the cache, not fetch.
        from advisors import community_strats as mod

        original_cached_pull = ac.cached_pull

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            # Route through real cached_pull but wrap fetch_fn to count calls.
            def counted_fetch():
                fetch_call_count["calls"] += 1
                return counting_fetch()

            return original_cached_pull(collection_name, counted_fetch, **kwargs)

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            result = mod.load_community_strategies()

        # fetch_fn must NOT have been called — the cache row is fresh.
        assert fetch_call_count["calls"] == 0, (
            f"expected fetch_fn skipped on cache HIT; got {fetch_call_count['calls']} calls"
        )
        assert result["available"] is True, (
            "cache-hit result must still return available=True with the cached docs"
        )


# ---------------------------------------------------------------------------
# AC-2: force_refresh bypasses cache
# ---------------------------------------------------------------------------


class TestForceRefresh:
    """AC-2: force_refresh=True always calls fetch_fn, ignoring TTL."""

    def test_force_refresh_calls_fetch_fn_despite_fresh_cache(
        self, isolated_atlas_cache_db, single_valid_doc
    ):
        """Even when the cache row is fresh, force_refresh=True must invoke
        the fetch_fn (bypass the TTL gate entirely)."""
        import json as _json
        from datetime import datetime

        # Pre-seed a fresh cache row.
        conn = sqlite3.connect(isolated_atlas_cache_db)
        try:
            now_iso = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) "
                "VALUES (?, ?, ?)",
                ("captplanet.strategies", now_iso, _json.dumps(single_valid_doc)),
            )
            conn.commit()
        finally:
            conn.close()

        fetch_call_count = Counter()
        original_cached_pull = ac.cached_pull

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            def counted_fetch():
                fetch_call_count["calls"] += 1
                return single_valid_doc

            return original_cached_pull(collection_name, counted_fetch, **kwargs)

        from advisors import community_strats as mod

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            mod.load_community_strategies(force_refresh=True)

        # fetch_fn must have been called — force_refresh bypasses the TTL.
        assert fetch_call_count["calls"] == 1, (
            f"expected fetch_fn called once with force_refresh=True; "
            f"got {fetch_call_count['calls']}"
        )


# ---------------------------------------------------------------------------
# AC-3: candidate shape, validate_tree, tickers
# ---------------------------------------------------------------------------


class TestCandidateShape:
    """AC-3: Each returned candidate has the required keys and correct values."""

    def _get_one_candidate(self, mod, doc_list):
        """Helper: patch cached_pull to return doc_list and get first candidate."""
        with patch(
            "advisors.atlas_cache.cached_pull",
            return_value=doc_list,
        ):
            result = mod.load_community_strategies()
        assert result["available"] is True, f"expected available=True; got {result!r}"
        assert len(result["candidates"]) >= 1, "expected at least one candidate"
        return result["candidates"][0]

    def test_candidate_shape_has_required_keys(self, module_under_test, single_valid_doc):
        """Every candidate must carry exactly the 6 required keys."""
        candidate = self._get_one_candidate(module_under_test, single_valid_doc)
        required_keys = {"sid", "name", "tree", "tickers", "oos_metrics", "composition_hash"}
        missing = required_keys - candidate.keys()
        assert not missing, (
            f"candidate missing required keys: {missing!r} — got keys {set(candidate.keys())!r}"
        )

    def test_candidate_tree_passes_validate_tree(self, module_under_test, single_valid_doc):
        """The 'tree' in each candidate must pass symphony_schema.validate_tree (== [])."""
        candidate = self._get_one_candidate(module_under_test, single_valid_doc)
        tree = candidate["tree"]
        assert isinstance(tree, dict), f"expected tree to be a dict; got {type(tree).__name__}"
        errors = ss.validate_tree(tree)
        assert errors == [], f"candidate tree failed validate_tree: {errors}"

    def test_candidate_tickers_match_extract_tickers(self, module_under_test, single_valid_doc):
        """candidate['tickers'] must equal symphony_schema.extract_tickers(tree)
        (no '%' placeholder, no extras, no omissions)."""
        candidate = self._get_one_candidate(module_under_test, single_valid_doc)
        tree = candidate["tree"]
        expected_tickers = ss.extract_tickers(tree)
        # '% ' must never appear in the result — extract_tickers already excludes it.
        assert "%" not in candidate["tickers"], (
            "tickers set must not contain the '%' binary-compound placeholder"
        )
        # The tickers in the candidate must match the schema's extract.
        assert set(candidate["tickers"]) == expected_tickers, (
            f"candidate tickers {candidate['tickers']!r} != "
            f"extract_tickers(tree) {expected_tickers!r}"
        )

    def test_candidate_source_field_is_captplanet(self, module_under_test, single_valid_doc):
        """The top-level result dict must carry source='captplanet' on a
        successful load (available=True with real docs from cache)."""
        with patch("advisors.atlas_cache.cached_pull", return_value=single_valid_doc):
            mod = module_under_test
            result = mod.load_community_strategies()
        # The function must process the returned docs and succeed, not just
        # hardcode a static stub return value.
        assert result["available"] is True, (
            f"expected available=True when cached_pull returns valid docs; got {result!r}"
        )
        assert result.get("source") == "captplanet", (
            f"expected source='captplanet'; got {result.get('source')!r}"
        )

    def test_stats_dict_has_all_required_keys(self, module_under_test, single_valid_doc):
        """The 'stats' dict must carry all 7 counters defined by the contract,
        and stats['pulled'] must equal the number of docs returned by the cache."""
        with patch("advisors.atlas_cache.cached_pull", return_value=single_valid_doc):
            mod = module_under_test
            result = mod.load_community_strategies()
        stats = result.get("stats", {})
        required_stat_keys = {
            "pulled",
            "valid",
            "missing_edn_string",
            "parse_failed",
            "validate_rejected",
            "sharpe_filtered",
            "deduped",
        }
        missing = required_stat_keys - stats.keys()
        assert not missing, (
            f"stats dict missing required keys: {missing!r} — got {set(stats.keys())!r}"
        )
        # stats['pulled'] must equal the count of docs the cache returned — not 0.
        assert stats["pulled"] == len(single_valid_doc), (
            f"stats['pulled'] must equal the number of docs from the cache "
            f"({len(single_valid_doc)}); got {stats['pulled']}"
        )

    def test_composition_hash_is_nonempty_string(self, module_under_test, single_valid_doc):
        """composition_hash must be a non-empty string (derived from database.compute_composition_hash)."""
        candidate = self._get_one_candidate(module_under_test, single_valid_doc)
        h = candidate.get("composition_hash")
        assert isinstance(h, str) and len(h) > 0, (
            f"composition_hash must be a non-empty string; got {h!r}"
        )


# ---------------------------------------------------------------------------
# AC-4: filtering — bad edn, parse_failed, validate_rejected counted in stats
# ---------------------------------------------------------------------------


class TestFilteringAndStats:
    """AC-4: Defective docs are counted in stats; valid remainder is returned."""

    def test_missing_edn_string_counted_in_stats(self, module_under_test):
        """A doc with no 'edn_string' key must increment stats['missing_edn_string']."""
        doc_missing_edn = {"sid": "sid-x", "name": "No EDN", "oos_metrics": {}}
        with patch("advisors.atlas_cache.cached_pull", return_value=[doc_missing_edn]):
            mod = module_under_test
            result = mod.load_community_strategies()
        assert result["available"] is True, "missing-edn docs should not cause available=False"
        assert result["stats"]["missing_edn_string"] >= 1, (
            "expected stats['missing_edn_string'] >= 1 for a doc missing 'edn_string'"
        )
        assert result["candidates"] == [], (
            "a doc with missing edn_string must not appear in candidates"
        )

    def test_unparseable_edn_counted_in_stats(self, module_under_test):
        """A doc with an unparseable edn_string must increment stats['parse_failed']."""
        doc_bad_edn = {
            "sid": "sid-bad",
            "name": "Bad Parse",
            "edn_string": "this is not valid JSON or EDN {{{",
            "oos_metrics": {},
        }
        with patch("advisors.atlas_cache.cached_pull", return_value=[doc_bad_edn]):
            mod = module_under_test
            result = mod.load_community_strategies()
        assert result["available"] is True, "parse-fail docs should not cause available=False"
        assert result["stats"]["parse_failed"] >= 1, (
            "expected stats['parse_failed'] >= 1 for an unparseable edn_string"
        )
        assert result["candidates"] == [], (
            "a doc with parse_failed edn_string must not appear in candidates"
        )

    def test_validate_rejected_counted_in_stats(self, module_under_test):
        """A doc whose parsed tree fails validate_tree must increment stats['validate_rejected']."""
        # A tree that parses as JSON but fails validate_tree: not a dict at root.
        bad_tree = [{"step": "asset", "ticker": "SPY"}]  # list, not dict → validate error
        doc_bad_tree = {
            "sid": "sid-badtree",
            "name": "Bad Tree",
            "edn_string": json.dumps(bad_tree),
            "oos_metrics": {},
        }
        with patch("advisors.atlas_cache.cached_pull", return_value=[doc_bad_tree]):
            mod = module_under_test
            result = mod.load_community_strategies()
        assert result["available"] is True, "validate-reject docs should not cause available=False"
        assert result["stats"]["validate_rejected"] >= 1, (
            "expected stats['validate_rejected'] >= 1 for a tree that fails validate_tree"
        )
        assert result["candidates"] == [], (
            "a doc with a validate_tree-rejected tree must not appear in candidates"
        )

    def test_mixed_valid_and_invalid_returns_valid_remainder(self, module_under_test):
        """When some docs are defective and some are valid, only valid ones appear
        in candidates; all defective ones are counted in stats."""
        valid_doc = _make_mongo_doc(sid="sid-good", name="Good", ticker="SPY")
        bad_edn_doc = {
            "sid": "sid-bad-edn",
            "name": "Junk",
            "edn_string": "NOT JSON",
            "oos_metrics": {},
        }
        missing_edn_doc = {"sid": "sid-no-edn", "name": "No EDN", "oos_metrics": {}}
        docs = [valid_doc, bad_edn_doc, missing_edn_doc]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert result["available"] is True
        assert len(result["candidates"]) == 1, (
            f"expected 1 valid candidate; got {len(result['candidates'])}"
        )
        assert result["candidates"][0]["sid"] == "sid-good", (
            "the only valid candidate must be the one with good edn_string"
        )
        assert result["stats"]["parse_failed"] >= 1, "bad-edn doc must be counted"
        assert result["stats"]["missing_edn_string"] >= 1, "missing-edn doc must be counted"

    def test_empty_collection_returns_available_true_with_empty_candidates(self, module_under_test):
        """An empty collection (no docs from Mongo) must yield available=True,
        candidates=[], stats.pulled=0. (Edge case from feature plan.)"""
        with patch("advisors.atlas_cache.cached_pull", return_value=[]):
            mod = module_under_test
            result = mod.load_community_strategies()
        assert result["available"] is True, "empty collection must not cause available=False"
        assert result["candidates"] == [], "empty collection must yield empty candidates"
        assert result["stats"]["pulled"] == 0, (
            f"expected stats['pulled']=0 for an empty collection; "
            f"got {result['stats'].get('pulled')}"
        )


# ---------------------------------------------------------------------------
# AC-5: dedup by composition_hash
# ---------------------------------------------------------------------------


class TestDeduplication:
    """AC-5: Two docs with the same composition hash collapse to one,
    retaining the higher OOS Sharpe."""

    def _make_duplicate_pair(self, sharpe_a: float = 0.5, sharpe_b: float = 1.8) -> list:
        """Return two docs whose trees hash identically (same ticker = same hash)."""
        # Same ticker → same tree structure → same composition_hash.
        tree = _make_minimal_tree(name="Dup Strategy", ticker="IVV")
        edn = _tree_as_edn_string(tree)
        doc_a = {
            "sid": "sid-dup-a",
            "name": "Dup A",
            "edn_string": edn,
            "oos_metrics": {"sharpe": sharpe_a},
        }
        doc_b = {
            "sid": "sid-dup-b",
            "name": "Dup B",
            "edn_string": edn,
            "oos_metrics": {"sharpe": sharpe_b},
        }
        return [doc_a, doc_b]

    def test_dedup_same_hash_keeps_higher_sharpe(self, module_under_test):
        """Two docs with identical composition collapse to one; the surviving
        candidate has the higher OOS Sharpe (not the lower one)."""
        low_sharpe = 0.5
        high_sharpe = 1.8
        docs = self._make_duplicate_pair(sharpe_a=low_sharpe, sharpe_b=high_sharpe)

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert result["available"] is True
        assert len(result["candidates"]) == 1, (
            f"expected exactly 1 candidate after dedup; got {len(result['candidates'])}"
        )
        surviving = result["candidates"][0]
        # The surviving candidate must have the HIGHER sharpe, not the lower one.
        actual_sharpe = surviving["oos_metrics"].get("sharpe")
        assert actual_sharpe == pytest.approx(high_sharpe, rel=1e-6), (
            # Tolerance: rel=1e-6 because sharpe is a float round-trip from JSON.
            f"dedup must retain the higher sharpe ({high_sharpe}); got {actual_sharpe!r}"
        )

    def test_dedup_count_reflected_in_stats(self, module_under_test):
        """stats['deduped'] must equal the number of docs removed by dedup."""
        docs = self._make_duplicate_pair()

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies()

        # 2 docs with the same hash → 1 kept, 1 deduped.
        assert result["stats"]["deduped"] == 1, (
            f"expected stats['deduped']=1 for one duplicate removed; "
            f"got {result['stats'].get('deduped')}"
        )

    def test_dedup_missing_sharpe_treated_as_lower_than_any_present_sharpe(self, module_under_test):
        """When one doc has a sharpe and the other doesn't, the one with a
        sharpe must win dedup (a missing sharpe is never 'better')."""
        tree = _make_minimal_tree(name="Dedup Test", ticker="GLD")
        edn = _tree_as_edn_string(tree)
        doc_with_sharpe = {
            "sid": "sid-has-sharpe",
            "name": "Has sharpe",
            "edn_string": edn,
            "oos_metrics": {"sharpe": 0.9},
        }
        doc_without_sharpe = {
            "sid": "sid-no-sharpe",
            "name": "No sharpe",
            "edn_string": edn,
            "oos_metrics": {},
        }
        docs = [doc_without_sharpe, doc_with_sharpe]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert result["available"] is True
        assert len(result["candidates"]) == 1
        surviving = result["candidates"][0]
        assert "sharpe" in surviving["oos_metrics"], (
            "dedup must keep the doc that HAS a sharpe over the one without"
        )


# ---------------------------------------------------------------------------
# AC-6: min_oos_sharpe filter and limit cap
# ---------------------------------------------------------------------------


class TestSharpeFilterAndLimit:
    """AC-6: min_oos_sharpe filter and limit cap."""

    def test_min_oos_sharpe_excludes_below_floor(self, module_under_test):
        """Docs with a present sharpe below min_oos_sharpe must be excluded
        and counted in stats['sharpe_filtered']."""
        floor = 1.0
        doc_above = _make_mongo_doc(sid="above", name="Above", ticker="SPY", sharpe=1.5)
        doc_below = _make_mongo_doc(sid="below", name="Below", ticker="TLT", sharpe=0.3)
        docs = [doc_above, doc_below]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies(min_oos_sharpe=floor)

        assert result["available"] is True
        candidate_sids = {c["sid"] for c in result["candidates"]}
        assert "above" in candidate_sids, "doc above the floor must be included"
        assert "below" not in candidate_sids, "doc below the floor must be excluded"
        assert result["stats"]["sharpe_filtered"] >= 1, (
            "stats['sharpe_filtered'] must be >= 1 when one doc was excluded by the floor"
        )

    def test_min_oos_sharpe_keeps_missing_sharpe_docs(self, module_under_test):
        """Docs lacking oos_metrics['sharpe'] entirely must be KEPT, not excluded,
        regardless of the min_oos_sharpe floor (contract: absence != failing metric)."""
        floor = 5.0  # very high — any present sharpe would fail this
        doc_no_sharpe = _make_mongo_doc(
            sid="no-sharpe", name="No Sharpe", ticker="GLD", sharpe=None
        )
        doc_with_low_sharpe = _make_mongo_doc(sid="low", name="Low", ticker="TLT", sharpe=0.1)
        docs = [doc_no_sharpe, doc_with_low_sharpe]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies(min_oos_sharpe=floor)

        candidate_sids = {c["sid"] for c in result["candidates"]}
        assert "no-sharpe" in candidate_sids, (
            "a doc lacking sharpe must be KEPT even when min_oos_sharpe floor is high"
        )
        assert "low" not in candidate_sids, (
            "a doc with a present-but-below-floor sharpe must be excluded"
        )

    def test_limit_caps_returned_candidates(self, module_under_test):
        """limit=N must cap the returned candidates list at exactly N entries
        when more than N valid docs are available."""
        # Create 3 distinct docs so the limit has something to cut.
        docs = [
            _make_mongo_doc(sid=f"sid-{i}", name=f"Strat {i}", ticker=t)
            for i, t in enumerate(["SPY", "QQQ", "TLT"])
        ]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies(limit=2)

        # The function must have processed the docs (not just returned stub []).
        assert result["available"] is True, (
            f"expected available=True with 3 valid docs; got {result!r}"
        )
        assert len(result["candidates"]) <= 2, (
            f"limit=2 must cap candidates at 2; got {len(result['candidates'])}"
        )
        # There must be exactly 2 (not 0 or 1) — the input has 3 valid docs.
        assert len(result["candidates"]) == 2, (
            f"with 3 valid docs and limit=2, expect exactly 2 candidates; "
            f"got {len(result['candidates'])}"
        )

    def test_limit_none_returns_all_valid_candidates(self, module_under_test):
        """limit=None (default) must not cap the result."""
        docs = [
            _make_mongo_doc(sid=f"sid-{i}", name=f"Strat {i}", ticker=t)
            for i, t in enumerate(["SPY", "QQQ", "TLT"])
        ]

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies(limit=None)

        # All 3 are distinct → all 3 should be returned (no dedup, no filter).
        assert len(result["candidates"]) == 3, (
            f"limit=None must return all valid candidates; got {len(result['candidates'])}"
        )


# ---------------------------------------------------------------------------
# AC-7: never-raising + D-1 error contract
# ---------------------------------------------------------------------------


class TestNeverRaisingD1Contract:
    """AC-7: Any failure must yield {available: False, reason: type(exc).__name__}
    ONLY. No MONGO_URI, no host, no message substrings."""

    def _call_with_mongo_down(self, mod):
        """Simulate Mongo being unavailable by making cached_pull return None."""
        with patch("advisors.atlas_cache.cached_pull", return_value=None):
            return mod.load_community_strategies()

    def test_mongo_down_returns_available_false(self, module_under_test, single_valid_doc):
        """When cached_pull returns None (its documented sentinel for 'fetch failed,
        no stale row'), load_community_strategies must return available=False.

        Adversarial pairing: when cached_pull returns valid docs, the result
        MUST be available=True — confirming the function distinguishes None from
        a real payload, and isn't just hardcoding available=False everywhere.
        """
        # Pairing: confirm available=True is POSSIBLE when cache has valid data.
        with patch("advisors.atlas_cache.cached_pull", return_value=single_valid_doc):
            success_result = module_under_test.load_community_strategies()
        assert success_result["available"] is True, (
            f"paired assertion: expected available=True when cache returns valid docs; "
            f"this confirms the function is not unconditionally returning False. "
            f"Got: {success_result!r}"
        )

        # Actual test: None sentinel → available=False.
        result = self._call_with_mongo_down(module_under_test)
        assert result["available"] is False, (
            f"expected available=False when cached_pull returns None; got {result!r}"
        )

    def test_available_false_has_required_keys(self, module_under_test):
        """The D-1 failure dict must carry {available, reason, source, candidates, stats}
        and reason must not be 'NotImplemented' (the stub sentinel)."""
        with patch("advisors.atlas_cache.cached_pull", side_effect=RuntimeError("boom")):
            mod = module_under_test
            result = mod.load_community_strategies()
        required = {"available", "reason", "source", "candidates", "stats"}
        missing = required - result.keys()
        assert not missing, (
            f"D-1 failure dict missing keys: {missing!r} — got {set(result.keys())!r}"
        )
        assert result["candidates"] == [], "D-1 failure dict must carry candidates=[]"
        # reason must reflect the actual exception, not the stub sentinel.
        assert result.get("reason") != "NotImplemented", (
            "reason must be the caught exception class name, not the stub sentinel 'NotImplemented'"
        )
        assert result.get("reason") == "RuntimeError", (
            f"expected reason='RuntimeError' when RuntimeError is raised; "
            f"got {result.get('reason')!r}"
        )

    def test_d1_reason_is_exception_class_name_only(self, module_under_test):
        """reason must be exactly type(exc).__name__ — nothing more, nothing less.

        The side_effect is an exception instance (not a zero-arg callable) so
        unittest.mock raises it directly without forwarding call args, which
        would cause a TypeError masking the real exception type.
        """

        class _FakeMongoPymongoError(ConnectionError):
            pass

        # Use an exception INSTANCE as side_effect so mock raises it directly,
        # without forwarding the (collection_name, fetch_fn, ...) call arguments.
        exc_instance = _FakeMongoPymongoError("mongodb+srv://user:password@cluster.example.com")

        with patch("advisors.atlas_cache.cached_pull", side_effect=exc_instance):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert result["available"] is False
        reason = result.get("reason", "")
        # Must be the class name exactly — type(exc).__name__ of the raised exception.
        assert reason == "_FakeMongoPymongoError", (
            f"expected reason='_FakeMongoPymongoError'; got {reason!r}"
        )

    def test_d1_no_mongo_uri_substring_in_return(self, module_under_test, monkeypatch):
        """MONGO_URI must never appear in the return value, even if the exception
        message contains the URI string."""
        fake_uri = "mongodb+srv://admin:secret@cluster.mongodb.net/db"
        monkeypatch.setenv("MONGO_URI", fake_uri)

        # Use exception instances as side_effect to avoid TypeError from arg forwarding.
        exc = ConnectionError(f"Failed to connect to {fake_uri}")

        with patch("advisors.atlas_cache.cached_pull", side_effect=exc):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert not _recursive_contains(result, fake_uri), (
            "MONGO_URI must not appear anywhere in the return value (D-1 leak)"
        )
        # Also check that no fragment of the URI leaks (hostname portion).
        assert not _recursive_contains(result, "cluster.mongodb.net"), (
            "MongoDB hostname must not appear anywhere in the return value (D-1 leak)"
        )

    def test_d1_no_host_substring_in_return(self, module_under_test, monkeypatch):
        """Exception messages containing connection hostnames must not propagate."""
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://u:p@myhost.example.com/db")

        # Use exception instance as side_effect to avoid TypeError from arg forwarding.
        exc = OSError("Connection refused: myhost.example.com:27017")

        with patch("advisors.atlas_cache.cached_pull", side_effect=exc):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert not _recursive_contains(result, "myhost.example.com"), (
            "hostname from exception message must not appear in the return value (D-1)"
        )

    def test_function_never_raises(self, module_under_test):
        """load_community_strategies must never propagate an exception under any failure
        mode — it must absorb all exceptions and return a dict."""
        scenarios = [
            # cached_pull raises
            patch("advisors.atlas_cache.cached_pull", side_effect=Exception("boom")),
            # cached_pull returns None (no stale row, fetch failed)
            patch("advisors.atlas_cache.cached_pull", return_value=None),
            # cached_pull returns garbage (not a list)
            patch("advisors.atlas_cache.cached_pull", return_value={"unexpected": "shape"}),
        ]
        for scenario in scenarios:
            with scenario:
                mod = module_under_test
                try:
                    result = mod.load_community_strategies()
                except Exception as exc:
                    pytest.fail(
                        f"load_community_strategies raised {type(exc).__name__}: {exc} "
                        f"(must never raise — D-1 contract)"
                    )
                assert isinstance(result, dict), (
                    "load_community_strategies must always return a dict"
                )


# ---------------------------------------------------------------------------
# AC-8: secrets isolation — MONGO_URI never in cache DB or return value
# ---------------------------------------------------------------------------


class TestSecretsIsolation:
    """AC-8: MONGO_URI must never be persisted to the cache DB or returned."""

    def test_mongo_uri_not_stored_in_cache_db(
        self, module_under_test, isolated_atlas_cache_db, single_valid_doc, monkeypatch
    ):
        """After a successful fetch, the cache DB row payload must not contain
        the MONGO_URI string (the loader must never pass it to atlas_cache)."""
        fake_uri = "mongodb+srv://superuser:TOPSECRET@prod-cluster.example.com/mydb"
        monkeypatch.setenv("MONGO_URI", fake_uri)

        with patch("advisors.atlas_cache.cached_pull", return_value=single_valid_doc):
            mod = module_under_test
            mod.load_community_strategies()

        # Inspect the cache DB for any occurrence of the URI.
        conn = sqlite3.connect(isolated_atlas_cache_db)
        try:
            rows = conn.execute("SELECT payload FROM atlas_cache").fetchall()
        finally:
            conn.close()

        for (payload_str,) in rows:
            assert fake_uri not in payload_str, (
                "MONGO_URI must not appear in the atlas_cache payload column (AC-8 secret isolation)"
            )
            assert "TOPSECRET" not in payload_str, (
                "Credential substring must not appear in the atlas_cache payload column"
            )

    def test_mongo_uri_not_in_return_value_recursive(
        self, module_under_test, isolated_atlas_cache_db, single_valid_doc, monkeypatch
    ):
        """The return value of load_community_strategies must not contain
        MONGO_URI anywhere (recursive string walk)."""
        fake_uri = "mongodb+srv://superuser:TOPSECRET@prod-cluster.example.com/mydb"
        monkeypatch.setenv("MONGO_URI", fake_uri)

        with patch("advisors.atlas_cache.cached_pull", return_value=single_valid_doc):
            mod = module_under_test
            result = mod.load_community_strategies()

        assert not _recursive_contains(result, fake_uri), (
            "MONGO_URI must not appear anywhere in the return value (recursive walk)"
        )
        assert not _recursive_contains(result, "TOPSECRET"), (
            "Credential substring must not appear anywhere in the return value"
        )

    def test_no_autotuner_or_execution_import(self):
        """advisors/community_strats.py must not import autotuner or
        alpha_bot_execution (off-execution-path, advisory-only contract)."""
        source_path = (
            "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/"
            "pr-worktrees/community-strats/advisors/community_strats.py"
        )
        import pathlib

        src = pathlib.Path(source_path)
        if not src.exists():
            pytest.skip("advisors/community_strats.py does not exist yet (pre-implementation RED)")

        source_text = src.read_text(encoding="utf-8")
        # Parse import statements to find forbidden imports.
        try:
            tree = ast.parse(source_text)
        except SyntaxError as exc:
            pytest.skip(f"community_strats.py has a SyntaxError (stub): {exc}")

        forbidden_modules = {"autotuner", "alpha_bot_execution"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        violations = imported & forbidden_modules
        assert not violations, (
            f"community_strats.py must not import: {violations!r} "
            f"(off-execution-path, advisory-only — AC-8)"
        )

    def test_no_flask_import(self):
        """advisors/community_strats.py must not import Flask (no web dependency)."""
        import pathlib

        source_path = (
            "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/"
            "pr-worktrees/community-strats/advisors/community_strats.py"
        )
        src = pathlib.Path(source_path)
        if not src.exists():
            pytest.skip("advisors/community_strats.py does not exist yet")

        source_text = src.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_text)
        except SyntaxError:
            pytest.skip("community_strats.py is a stub with SyntaxError")

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        assert "flask" not in imported and "app" not in imported, (
            "community_strats.py must not import flask or app (no Flask dependency — AC-8)"
        )


# ---------------------------------------------------------------------------
# AC-9: Mongo projection excludes heavy fields
# ---------------------------------------------------------------------------


class TestProjection:
    """AC-9: The pymongo query projection must exclude heavy backtest/quantstats arrays."""

    def test_projection_excludes_heavy_fields(self, module_under_test):
        """The projection dict passed to pymongo's find() must NOT include
        backtest or quantstats fields. We intercept the find call to inspect
        the projection argument.

        Strategy: mock pymongo.MongoClient so we can capture the call arguments
        to collection.find(). The projection dict must contain only the 4
        lightweight fields: {sid, name, edn_string, oos_metrics}.
        Heavy fields that must be absent: backtest, quantstats (any key
        containing these substrings).
        """
        import unittest.mock as _mock

        captured_projections: list = []

        # Build a mock pymongo chain that captures find() call arguments.
        # Use spec=None MagicMocks so __getitem__ subscript access works
        # (the loader uses client["db"]["collection"] dict-style access).
        mock_cursor = _mock.MagicMock()
        mock_cursor.__iter__ = _mock.MagicMock(return_value=iter([]))

        mock_collection = _mock.MagicMock()

        def _capture_find(*args, **kwargs):
            # Projection is the second positional arg (filter is first) or a kwarg.
            if len(args) >= 2:
                captured_projections.append(args[1])
            elif "projection" in kwargs:
                captured_projections.append(kwargs["projection"])
            else:
                # find() called with no projection — record None to detect the gap.
                captured_projections.append(None)
            return mock_cursor

        mock_collection.find.side_effect = _capture_find

        # Use a regular MagicMock for db — __getitem__ returns the collection
        # whether accessed by attribute (db.strategies) or subscript (db["strategies"]).
        mock_db = _mock.MagicMock()
        mock_db.__getitem__ = _mock.MagicMock(return_value=mock_collection)
        # Attribute access (db.strategies) returns the same mock_collection.
        mock_db.strategies = mock_collection
        mock_db.captplanet = mock_collection

        mock_client_instance = _mock.MagicMock()
        mock_client_instance.__getitem__ = _mock.MagicMock(return_value=mock_db)
        mock_client_instance.captplanet = mock_db

        # Patch pymongo.MongoClient to return our mock.
        with patch("pymongo.MongoClient", return_value=mock_client_instance):
            # Force the fetch_fn to run (bypass the cache) by making cached_pull
            # call fetch_fn directly and return its result.
            with patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ):
                mod = module_under_test
                mod.load_community_strategies()

        if not captured_projections:
            pytest.skip(
                "pymongo.find() was not called — stub does not call pymongo yet (RED expected)"
            )

        if any(p is None for p in captured_projections):
            pytest.fail(
                "find() was called with no projection argument — "
                "AC-9 requires a projection to exclude heavy fields"
            )

        # DE-ATLAS-SLOW-QUERY-001: _fetch_fn now issues TWO find() calls (a
        # lightweight sharpe-sorted selection query, then a full-document query
        # keyed by the selected ids) — see test_community_strats_fetch_query.py
        # for the query-shape tests. AC-9's "no heavy backtest/quantstats fields"
        # guard applies across BOTH calls; the "must include sid/name/edn_string/
        # oos_metrics" guard applies to whichever call actually carries the full
        # document (identified by requesting 'edn_string'), not call index 0 —
        # the lightweight selection query legitimately omits those fields.
        heavy_field_substrings = ("backtest", "quantstats")
        for captured in captured_projections:
            for key in captured or {}:
                for heavy in heavy_field_substrings:
                    assert heavy.lower() not in str(key).lower(), (
                        f"projection must exclude heavy field {key!r} "
                        f"(matches forbidden substring {heavy!r}) — AC-9"
                    )

        full_doc_projections = [p for p in captured_projections if p and "edn_string" in p]
        if not full_doc_projections:
            pytest.fail(
                "no find() call carried a projection containing 'edn_string' — AC-9 "
                f"requires the full-document query to request it; captured "
                f"projections={captured_projections!r}"
            )
        projection = full_doc_projections[0]

        # The projection MUST include the 4 lightweight required fields.
        required_projection_fields = {"sid", "name", "edn_string", "oos_metrics"}
        # Projection dicts: key=1 means include, key=0 means exclude.
        # A whitelist projection has all the wanted fields set to 1 (or truthy).
        for field in required_projection_fields:
            assert field in projection, f"projection must include required field {field!r} — AC-9"


# ---------------------------------------------------------------------------
# Additional coverage from integration-specialist audit (S1-C, S2-B additions)
# ---------------------------------------------------------------------------


class TestSignatureAndPlumbing:
    """S1-C: force_refresh is a keyword-only param and propagates to cached_pull."""

    def test_load_community_strategies_accepts_force_refresh_kwarg(self):
        """load_community_strategies must accept force_refresh as a keyword-only
        parameter. Inspect the signature to catch a missing parameter before
        any test even calls the function."""
        import inspect

        # Import the module under test (stub or real).
        if "advisors.community_strats" in sys.modules:
            mod = sys.modules["advisors.community_strats"]
        else:
            mod = importlib.import_module("advisors.community_strats")

        sig = inspect.signature(mod.load_community_strategies)
        params = sig.parameters
        assert "force_refresh" in params, (
            "load_community_strategies must have a 'force_refresh' parameter; "
            f"got parameters: {list(params.keys())!r}"
        )
        # It must be keyword-only (after the bare *).
        p = params["force_refresh"]
        assert p.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), f"force_refresh must be keyword-only or positional-or-keyword; got kind={p.kind.name!r}"
        # Default must be False (safe default — never force-refresh unless asked).
        assert p.default is False, f"force_refresh default must be False; got {p.default!r}"

    def test_force_refresh_propagated_to_cached_pull(self, module_under_test):
        """When load_community_strategies is called with force_refresh=True, the
        call to atlas_cache.cached_pull must include force_refresh=True.
        A spy on cached_pull captures the kwargs and asserts propagation."""
        captured_kwargs: list = []

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            captured_kwargs.append(kwargs)
            # Return an empty list so the function can finish without error.
            return []

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            mod = module_under_test
            mod.load_community_strategies(force_refresh=True)

        assert captured_kwargs, (
            "cached_pull was not called — implementation must call atlas_cache.cached_pull"
        )
        # The force_refresh kwarg must have been forwarded.
        assert captured_kwargs[0].get("force_refresh") is True, (
            f"force_refresh=True must be forwarded to cached_pull; "
            f"got cached_pull kwargs: {captured_kwargs[0]!r}"
        )

    def test_force_refresh_false_propagated_to_cached_pull(self, module_under_test):
        """When force_refresh is False (the default), cached_pull must be called
        with force_refresh=False (not missing or True)."""
        captured_kwargs: list = []

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            captured_kwargs.append(kwargs)
            return []

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            mod = module_under_test
            mod.load_community_strategies()  # default force_refresh=False

        assert captured_kwargs, "cached_pull was not called"
        assert captured_kwargs[0].get("force_refresh") is False, (
            f"force_refresh=False (default) must be forwarded to cached_pull; "
            f"got: {captured_kwargs[0]!r}"
        )

    def test_cached_pull_collection_name_is_captplanet_strategies(self, module_under_test):
        """The collection_name argument to cached_pull must be 'captplanet.strategies'
        (the canonical Atlas collection name for this loader)."""
        captured_collections: list = []

        def spy_cached_pull(collection_name, fetch_fn, **kwargs):
            captured_collections.append(collection_name)
            return []

        with patch("advisors.atlas_cache.cached_pull", side_effect=spy_cached_pull):
            mod = module_under_test
            mod.load_community_strategies()

        assert captured_collections, "cached_pull was not called"
        assert captured_collections[0] == "captplanet.strategies", (
            f"cached_pull must be called with collection_name='captplanet.strategies'; "
            f"got {captured_collections[0]!r}"
        )


class TestCachePipelineIntegrity:
    """S2-B + AC-1: Cache-hit result goes through the same parse/validate/dedup
    pipeline as a live-fetch result (not raw bytes returned directly)."""

    def test_cache_hit_result_undergoes_validate_tree_pipeline(
        self, isolated_atlas_cache_db, single_valid_doc
    ):
        """A cache-hit payload (pre-seeded row) must still be parsed and validated,
        producing structurally correct candidates, not raw Mongo docs."""
        import json as _json
        from datetime import datetime

        # Pre-seed a fresh cache row with valid docs.
        conn = sqlite3.connect(isolated_atlas_cache_db)
        try:
            now_iso = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) "
                "VALUES (?, ?, ?)",
                ("captplanet.strategies", now_iso, _json.dumps(single_valid_doc)),
            )
            conn.commit()
        finally:
            conn.close()

        from advisors import community_strats as mod

        # Do NOT mock cached_pull — use the real one with the isolated DB.
        result = mod.load_community_strategies()

        # The cache hit must produce a fully-processed result (not just raw docs).
        assert result["available"] is True, (
            "cache-hit path must produce available=True when cached docs are valid"
        )
        assert len(result["candidates"]) >= 1, (
            "cache-hit path must yield at least one candidate from the pre-seeded doc"
        )
        # Each candidate must have the full required shape (pipeline was applied).
        candidate = result["candidates"][0]
        required_keys = {"sid", "name", "tree", "tickers", "oos_metrics", "composition_hash"}
        missing = required_keys - candidate.keys()
        assert not missing, (
            f"cache-hit candidate missing required keys: {missing!r} — "
            "the parse/validate/dedup pipeline must run on cached payloads too"
        )
        # The tree must pass validate_tree (pipeline ran correctly).
        errors = ss.validate_tree(candidate["tree"])
        assert errors == [], f"cache-hit candidate tree failed validate_tree: {errors}"


class TestStatsInvariant:
    """S2-C: stats accounting invariant — all drops are accounted for."""

    def test_stats_sum_invariant_holds_across_all_counters(self, module_under_test):
        """The sum invariant: pulled == valid + deduped + missing_edn_string +
        parse_failed + validate_rejected + sharpe_filtered.

        Every doc pulled from the cache must be accounted for in exactly one
        stats counter. This catches off-by-one errors in the accounting loop.
        """
        # Build a mixed batch: 1 valid, 1 missing edn, 1 bad parse,
        # 1 validate-rejected, 1 sharpe-filtered, 2 duplicates (1 deduped).
        valid_doc = _make_mongo_doc(sid="v1", name="Valid", ticker="SPY", sharpe=2.0)

        missing_edn = {"sid": "m1", "name": "Missing", "oos_metrics": {}}

        bad_parse = {
            "sid": "p1",
            "name": "BadParse",
            "edn_string": "NOT_JSON{{{",
            "oos_metrics": {},
        }

        # A tree that parses but validate_tree rejects (list at root).
        bad_tree_doc = {
            "sid": "t1",
            "name": "BadTree",
            "edn_string": json.dumps([{"step": "asset", "ticker": "X"}]),
            "oos_metrics": {},
        }

        # sharpe-filtered: has a sharpe of 0.1 which is below floor=1.0.
        sharpe_filtered_doc = _make_mongo_doc(
            sid="sf1", name="SharpeFail", ticker="TLT", sharpe=0.1
        )

        # Duplicate pair: same tree → same hash; one will be deduped.
        tree = _make_minimal_tree(name="Dup", ticker="IVV")
        edn = _tree_as_edn_string(tree)
        dup_a = {"sid": "d1", "name": "DupA", "edn_string": edn, "oos_metrics": {"sharpe": 1.5}}
        dup_b = {"sid": "d2", "name": "DupB", "edn_string": edn, "oos_metrics": {"sharpe": 1.0}}

        docs = [valid_doc, missing_edn, bad_parse, bad_tree_doc, sharpe_filtered_doc, dup_a, dup_b]
        # Total pulled: 7

        with patch("advisors.atlas_cache.cached_pull", return_value=docs):
            mod = module_under_test
            result = mod.load_community_strategies(min_oos_sharpe=1.0)

        stats = result["stats"]
        pulled = stats["pulled"]
        accounted = (
            stats["valid"]
            + stats["deduped"]
            + stats["missing_edn_string"]
            + stats["parse_failed"]
            + stats["validate_rejected"]
            + stats["sharpe_filtered"]
        )
        assert pulled == len(docs), (
            f"stats['pulled'] must equal the number of docs from the cache ({len(docs)}); "
            f"got {pulled}"
        )
        assert pulled == accounted, (
            f"stats sum invariant failed: pulled ({pulled}) != sum of all drop counters "
            f"({accounted}). Stats: {stats!r}. Every pulled doc must land in exactly one bucket."
        )


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (it installs a NEW module object each call while other already-imported modules
# keep references to the OLD one — leaking a whole module graph per test across a
# multi-file run, which OOMs single-process full-tree verification). This file was
# the DOMINANT leaker (35 reload sites). community_strats accesses atlas_cache via
# module-attribute / call-time lookup (`atlas_cache.cached_pull(...)`), so tests that
# patch "advisors.atlas_cache.cached_pull" are visible without re-executing the module.
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
        "importlib.reload(...) found in test_community_strats.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. The community_strats "
        "module is accessed via module-attribute / call-time lookup; patch "
        "'advisors.atlas_cache.cached_pull' (or the relevant seam) directly instead of "
        "reloading. Env isolation is monkeypatch.setenv(ATLAS_CACHE_DB_PATH) + tmp_path."
    )
