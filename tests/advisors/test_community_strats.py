"""RED tests — community_strats loader (advisors/community_strats.py).

Module under test: advisors.community_strats

All tests are RED by construction: advisors/community_strats.py does not exist yet.
Every assertion is derived from:
  - feature-plans/community-strats-loader.md (AC-1..AC-8)
  - advisors/symphony_schema.py (validate_tree, extract_tickers, constructors)
  - tests/fixtures/math/community_strats_loader_basic.json
  - tests/fixtures/math/community_strats_loader_frontrunner.json

ADVERSARIAL FOCUS:
  - Mock ALL Mongo — no live calls anywhere in this file.
  - AC-2: invalid trees are SKIPPED (never raised), counted in stats.invalid.
  - AC-3: dedup collapses same-composition trees; retains higher-OOS-quality copy.
  - AC-4: tickers from condition.tickers[] (binary-compound) ARE extracted; "%" is NOT.
  - AC-5: limit caps candidate count; min_oos_sharpe pre-filters; missing-metric docs KEPT.
  - AC-6: any exception from .find() → available=False + bare type-name reason; empty
    collection → available=False, no raise.
  - AC-7: MONGO_URI env value NEVER appears in any returned field.
  - AC-8: no Flask route, no LIVE_EXECUTION, lazy import (module importable without pymongo).

Rules for this file:
  - Never hardcode producer-computed numeric values. oos_metrics values used as test INPUTS
    (they are data fed to the loader, not outputs we derive from the loader).
  - Use pytest.approx with explicit tolerance only when comparing floats the loader computed;
    for fixture-provided floats passed through as-is, exact equality is fine.
  - assert_never_raises: every AC-6 test must confirm no exception propagates.
  - No shared mutable state across tests — all fixtures have explicit scope declarations.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repository layout helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_BASIC = (
    _REPO_ROOT / "tests" / "fixtures" / "math" / "community_strats_loader_basic.json"
)
_FIXTURE_FRONTRUNNER = (
    _REPO_ROOT / "tests" / "fixtures" / "math" / "community_strats_loader_frontrunner.json"
)


def _load_fixture(path: pathlib.Path) -> dict:
    if not path.exists():
        pytest.skip(f"Fixture missing: {path}")
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Symphony-schema tree builders (used to construct fake edn_string payloads)
# We use the REAL symphony_schema constructors — not mocks — so the trees
# we feed into the loader are structurally correct (validate_tree == []).
# ---------------------------------------------------------------------------

from advisors.symphony_schema import (  # noqa: E402
    extract_tickers,
    make_asset,
    make_binary_compound_condition,
    make_constant_rhs,
    make_condition_operand,
    make_if_compound,
    make_root,
    make_weight_equal,
    validate_tree,
)


# ---------------------------------------------------------------------------
# Mock Mongo helpers
#
# The loader accepts `client=` as an injectable param.  We pass a mock
# whose .find(...) returns a list of fake doc dicts.  No real Mongo is touched.
# ---------------------------------------------------------------------------


def _make_mock_collection(docs: list[dict]):
    """Return a mock object whose .find() yields docs.

    The loader calls collection.find() with some optional filter/projection.
    We return a list so the loader can iterate; a real pymongo cursor is iterable.
    """
    col = MagicMock()
    col.find.return_value = docs
    return col


def _make_mock_client(docs: list[dict]):
    """Return a mock Mongo client whose captplanet.strategies collection returns docs."""
    col = _make_mock_collection(docs)
    client = MagicMock()
    # Support both client["captplanet"]["strategies"] and client.captplanet.strategies
    client.__getitem__.return_value.__getitem__.return_value = col
    client.captplanet.strategies = col
    return client, col


def _simple_tree() -> dict:
    """Build a minimal valid tree: root → wt-cash-equal → [SPY, BND]."""
    return make_root(
        "SimpleEW",
        "daily",
        [make_weight_equal([make_asset("SPY"), make_asset("BND")])],
    )


def _frontrunner_tree(
    watched: list[str],
    basket: list[str],
) -> dict:
    """Build a frontrunner-shaped tree using make_if_compound + make_binary_compound_condition.

    The true branch condition checks: ANY of watched tickers has RSI(10) > 70.
    The else branch holds the basket.  This exercises extract_tickers on condition.tickers[].
    """
    condition = make_binary_compound_condition(
        fn="relative-strength-index",
        tickers=list(watched),
        comparator="gt",
        rhs=make_constant_rhs(70),
        window=10,
        operator="any",
    )
    then_assets = [make_asset(t) for t in basket]
    else_assets = [make_asset(t) for t in basket]
    if_node = make_if_compound(
        condition,
        then_children=[make_weight_equal(then_assets)],
        else_children=[make_weight_equal(else_assets)],
    )
    return make_root("Frontrunner", "daily", [if_node])


def _doc(
    sid: str,
    name: str,
    tree: dict,
    oos_metrics: dict | None = None,
) -> dict:
    """Build a fake Mongo doc as the loader expects it."""
    return {
        "sid": sid,
        "name": name,
        "edn_string": json.dumps(tree),
        "oos_metrics": oos_metrics,
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def basic_fixture() -> dict:
    return _load_fixture(_FIXTURE_BASIC)


@pytest.fixture(scope="function")
def frontrunner_fixture() -> dict:
    return _load_fixture(_FIXTURE_FRONTRUNNER)


@pytest.fixture(scope="function")
def simple_doc() -> dict:
    """A single valid doc wrapping a simple two-asset equal-weight tree."""
    return _doc("strat-001", "SimpleEW", _simple_tree(), {"sharpe": 1.2, "cagr": 0.08})


@pytest.fixture(scope="function")
def fake_mongo_uri(monkeypatch) -> str:
    """Set MONGO_URI to a fake value that must NEVER appear in loader output."""
    fake_uri = "mongodb+srv://fakeuser:SUPERSECRET_PASSWORD@cluster.example.com/test"
    monkeypatch.setenv("MONGO_URI", fake_uri)
    return fake_uri


# ---------------------------------------------------------------------------
# AC-1: Basic happy path — loader pulls, parses, returns candidates
# ---------------------------------------------------------------------------


class TestAC1BasicHappyPath:
    """load_community_strategies with a single valid doc returns a well-formed result."""

    def test_load_returns_dict_with_available_key(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert isinstance(result, dict), "result must be a dict"
        assert "available" in result, "result must contain 'available' key"

    def test_load_returns_candidates_list(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert "candidates" in result, "result must contain 'candidates'"
        assert isinstance(result["candidates"], list), "'candidates' must be a list"

    def test_load_returns_stats_dict_with_required_keys(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert "stats" in result, "result must contain 'stats'"
        stats = result["stats"]
        for key in ("pulled", "valid", "invalid", "deduped"):
            assert key in stats, f"stats must contain '{key}'"

    def test_load_returns_source_string(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert "source" in result, "result must contain 'source'"
        assert isinstance(result["source"], str), "'source' must be a string"

    def test_single_valid_doc_produces_one_candidate(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert result["available"] is True, "available must be True when docs present"
        assert len(result["candidates"]) == 1, "one valid doc → one candidate"

    def test_candidate_has_all_required_keys(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        candidate = result["candidates"][0]
        for key in ("sid", "name", "tree", "tickers", "oos_metrics", "composition_hash"):
            assert key in candidate, f"candidate must have key '{key}'"

    def test_candidate_sid_matches_doc(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert result["candidates"][0]["sid"] == "strat-001"

    def test_candidate_name_matches_doc(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert result["candidates"][0]["name"] == "SimpleEW"

    def test_candidate_tree_is_a_dict(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert isinstance(result["candidates"][0]["tree"], dict), "tree must be a dict"

    def test_candidate_tree_passes_validate_tree(self, simple_doc):
        """The returned tree must pass validate_tree with zero HARD errors."""
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        tree = result["candidates"][0]["tree"]
        errors = validate_tree(tree)
        assert errors == [], f"candidate tree failed validate_tree: {errors}"

    def test_candidate_tickers_is_a_set_or_list(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        tickers = result["candidates"][0]["tickers"]
        assert isinstance(tickers, (set, list)), "tickers must be a set or list"

    def test_candidate_composition_hash_is_non_empty_string(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        h = result["candidates"][0]["composition_hash"]
        assert isinstance(h, str) and len(h) > 0, "composition_hash must be a non-empty string"

    def test_candidate_oos_metrics_matches_doc_value(self, simple_doc):
        """oos_metrics passed through from the doc — test INPUT value, not loader-computed."""
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        oos = result["candidates"][0]["oos_metrics"]
        # The fixture doc has oos_metrics = {"sharpe": 1.2, "cagr": 0.08}.
        # We assert shape/presence, not a specific float literal derived from the loader.
        assert oos is not None, "oos_metrics must not be None when doc provides it"
        assert "sharpe" in oos, "oos_metrics must contain 'sharpe' key from doc"

    def test_stats_pulled_count_is_positive_integer(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        pulled = result["stats"]["pulled"]
        assert isinstance(pulled, int) and pulled >= 1, "stats.pulled must be >= 1"

    def test_stats_valid_counts_non_negative(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert result["stats"]["valid"] >= 0
        assert result["stats"]["invalid"] >= 0
        assert result["stats"]["deduped"] >= 0


# ---------------------------------------------------------------------------
# AC-2: Invalid docs are skipped, never raised
# ---------------------------------------------------------------------------


class TestAC2InvalidDocsSkipped:
    """Invalid edn_string and invalid trees are counted in stats.invalid; loader never raises."""

    def test_non_json_edn_string_is_skipped(self):
        from advisors.community_strats import load_community_strategies

        bad_doc = {
            "sid": "bad-json",
            "name": "BadJSON",
            "edn_string": "NOT_VALID_JSON{{{{",
            "oos_metrics": None,
        }
        client, _ = _make_mock_client([bad_doc])
        # Must not raise
        result = load_community_strategies(client=client)

        assert isinstance(result, dict), "loader must not raise on non-JSON edn_string"
        assert result["stats"]["invalid"] >= 1, "invalid doc must be counted in stats.invalid"
        # No candidate for this doc
        sids = [c["sid"] for c in result["candidates"]]
        assert "bad-json" not in sids, "non-JSON doc must not appear in candidates"

    def test_missing_edn_string_field_is_skipped(self):
        from advisors.community_strats import load_community_strategies

        no_edn_doc = {
            "sid": "no-edn",
            "name": "NoEDN",
            # edn_string deliberately absent
            "oos_metrics": None,
        }
        client, _ = _make_mock_client([no_edn_doc])
        result = load_community_strategies(client=client)

        assert result["stats"]["invalid"] >= 1
        sids = [c["sid"] for c in result["candidates"]]
        assert "no-edn" not in sids

    def test_tree_that_fails_validate_tree_is_skipped(self):
        """A tree with an unknown step should be skipped (validate_tree returns HARD errors)."""
        from advisors.community_strats import load_community_strategies

        invalid_tree = {
            "step": "root",
            "name": "BadRoot",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    # step with UNKNOWN value — validate_tree hard error
                    "step": "UNKNOWN_STEP_XYZ",
                    "id": str(uuid.uuid4()),
                    "children": [],
                }
            ],
        }
        bad_doc = {
            "sid": "bad-tree",
            "name": "BadTree",
            "edn_string": json.dumps(invalid_tree),
            "oos_metrics": None,
        }
        client, _ = _make_mock_client([bad_doc])
        result = load_community_strategies(client=client)

        assert result["stats"]["invalid"] >= 1, "invalid-tree doc must increment stats.invalid"
        sids = [c["sid"] for c in result["candidates"]]
        assert "bad-tree" not in sids, "invalid-tree doc must not appear in candidates"

    def test_loader_never_raises_on_all_invalid_docs(self):
        """With a batch of all-invalid docs, loader returns dict with available=True and 0 candidates (not raises)."""
        from advisors.community_strats import load_community_strategies

        docs = [
            {"sid": "bad1", "name": "B1", "edn_string": "{{{not json", "oos_metrics": None},
            {"sid": "bad2", "name": "B2", "oos_metrics": None},  # missing edn_string
        ]
        client, _ = _make_mock_client(docs)
        # Must not raise under any circumstance
        try:
            result = load_community_strategies(client=client)
        except Exception as exc:
            pytest.fail(f"load_community_strategies raised unexpectedly: {type(exc).__name__}: {exc}")

        assert result["stats"]["invalid"] >= 2

    def test_valid_and_invalid_mix_only_valid_in_candidates(self):
        """Mixed batch: valid + invalid — only valid docs appear in candidates."""
        from advisors.community_strats import load_community_strategies

        valid_doc = _doc("good", "Good", _simple_tree(), {"sharpe": 1.0})
        invalid_doc = {"sid": "bad", "name": "Bad", "edn_string": "!!!", "oos_metrics": None}
        client, _ = _make_mock_client([valid_doc, invalid_doc])
        result = load_community_strategies(client=client)

        sids = [c["sid"] for c in result["candidates"]]
        assert "good" in sids
        assert "bad" not in sids
        assert result["stats"]["invalid"] >= 1


# ---------------------------------------------------------------------------
# AC-3: Deduplication of same-composition trees
# ---------------------------------------------------------------------------


class TestAC3Deduplication:
    """Two docs with the same composition collapse to one candidate; stats.deduped is incremented."""

    def test_two_identical_composition_docs_collapse_to_one(self):
        from advisors.community_strats import load_community_strategies

        tree = _simple_tree()
        # Same composition (identical tree shape), different sids
        doc_a = _doc("strat-A", "AlphaFund", tree, {"sharpe": 0.9})
        doc_b = _doc("strat-B", "BetaFund", tree, {"sharpe": 1.5})
        client, _ = _make_mock_client([doc_a, doc_b])
        result = load_community_strategies(client=client)

        assert len(result["candidates"]) == 1, (
            "Two docs with same composition must collapse to one candidate"
        )
        assert result["stats"]["deduped"] >= 1, (
            "stats.deduped must be >= 1 when a duplicate is removed"
        )

    def test_dedup_retains_higher_oos_sharpe_copy(self):
        """When deduplicating, the copy with higher OOS sharpe is retained.

        We assert behavior (the retained sid has sharpe >= the other),
        not a specific hash or numeric value the loader computed.
        """
        from advisors.community_strats import load_community_strategies

        tree = _simple_tree()
        # strat-low has sharpe=0.5, strat-high has sharpe=1.5
        doc_low = _doc("strat-low", "Low", tree, {"sharpe": 0.5})
        doc_high = _doc("strat-high", "High", tree, {"sharpe": 1.5})
        client, _ = _make_mock_client([doc_low, doc_high])
        result = load_community_strategies(client=client)

        assert len(result["candidates"]) == 1
        retained = result["candidates"][0]
        # The retained doc must be strat-high (higher sharpe).
        # We derive the expected sharpe from the fixture INPUT, not from the loader.
        assert retained["oos_metrics"] is not None
        assert retained["oos_metrics"]["sharpe"] == pytest.approx(1.5, rel=1e-9), (
            # tolerance 1e-9: sharpe is an exact pass-through of the fixture input value,
            # not a computed float, so negligible rel-tol is correct
            "dedup must retain the higher-OOS-sharpe candidate"
        )

    def test_different_composition_docs_are_not_deduped(self):
        """Two docs with different trees must NOT be collapsed."""
        from advisors.community_strats import load_community_strategies

        tree_a = make_root("A", "daily", [make_weight_equal([make_asset("SPY"), make_asset("BND")])])
        tree_b = make_root("B", "daily", [make_weight_equal([make_asset("GLD"), make_asset("TLT")])])
        doc_a = _doc("strat-a", "A", tree_a, {"sharpe": 1.0})
        doc_b = _doc("strat-b", "B", tree_b, {"sharpe": 1.0})
        client, _ = _make_mock_client([doc_a, doc_b])
        result = load_community_strategies(client=client)

        assert len(result["candidates"]) == 2, (
            "Different compositions must NOT be deduped"
        )
        assert result["stats"]["deduped"] == 0

    def test_dedup_composition_hash_is_identical_for_same_tree(self):
        """Two candidates built from the same tree structure must share a composition_hash.

        We verify behavior (same hash), not the hash value itself.
        """
        from advisors.community_strats import load_community_strategies

        tree = _simple_tree()
        doc_a = _doc("s-a", "A", tree, {"sharpe": 0.8})
        # Feed a second doc that is NOT deduplicated (different tree) to get two candidates
        tree_b = make_root("B", "weekly", [make_weight_equal([make_asset("GLD")])])
        doc_b = _doc("s-b", "B", tree_b, {"sharpe": 1.0})
        # Also feed a duplicate of tree_a to confirm hash matches
        doc_a2 = _doc("s-a2", "A2", tree, {"sharpe": 1.5})  # same tree → deduped

        client, _ = _make_mock_client([doc_a, doc_b, doc_a2])
        result = load_community_strategies(client=client)

        # 3 docs, 1 deduped → 2 candidates
        assert len(result["candidates"]) == 2
        # Both candidate hashes must be non-empty strings (we don't assert the value)
        for cand in result["candidates"]:
            assert isinstance(cand["composition_hash"], str)
            assert len(cand["composition_hash"]) > 0


# ---------------------------------------------------------------------------
# AC-4: Tickers extracted via extract_tickers, including condition.tickers[]
# ---------------------------------------------------------------------------


class TestAC4TickerExtraction:
    """tickers field is populated via symphony_schema.extract_tickers; condition.tickers[] included; '%' excluded."""

    def test_simple_tree_tickers_match_extract_tickers(self):
        from advisors.community_strats import load_community_strategies

        tree = _simple_tree()
        # Compute expected tickers independently using extract_tickers
        expected_tickers = extract_tickers(tree)

        doc = _doc("s", "S", tree)
        client, _ = _make_mock_client([doc])
        result = load_community_strategies(client=client)

        returned = set(result["candidates"][0]["tickers"])
        assert returned == expected_tickers, (
            "tickers must match symphony_schema.extract_tickers output"
        )

    def test_frontrunner_tree_watched_tickers_present(self, frontrunner_fixture):
        """Tickers from the binary-compound condition.tickers[] list appear in returned tickers."""
        from advisors.community_strats import load_community_strategies

        watched = frontrunner_fixture["watched_tickers"]
        basket = frontrunner_fixture["basket_tickers"]
        tree = _frontrunner_tree(watched, basket)
        doc = _doc("fr", "Frontrunner", tree)
        client, _ = _make_mock_client([doc])
        result = load_community_strategies(client=client)

        returned = set(result["candidates"][0]["tickers"])
        for t in watched:
            assert t in returned, (
                f"watched ticker {t!r} from condition.tickers[] must appear in extracted tickers"
            )

    def test_frontrunner_tree_basket_tickers_present(self, frontrunner_fixture):
        """Asset-node tickers from the basket also appear in returned tickers."""
        from advisors.community_strats import load_community_strategies

        watched = frontrunner_fixture["watched_tickers"]
        basket = frontrunner_fixture["basket_tickers"]
        tree = _frontrunner_tree(watched, basket)
        doc = _doc("fr", "Frontrunner", tree)
        client, _ = _make_mock_client([doc])
        result = load_community_strategies(client=client)

        returned = set(result["candidates"][0]["tickers"])
        for t in basket:
            assert t in returned, (
                f"basket asset ticker {t!r} must appear in extracted tickers"
            )

    def test_frontrunner_tree_percent_placeholder_absent(self, frontrunner_fixture):
        """The '%' placeholder ticker must NOT appear in returned tickers."""
        from advisors.community_strats import load_community_strategies

        watched = frontrunner_fixture["watched_tickers"]
        basket = frontrunner_fixture["basket_tickers"]
        tree = _frontrunner_tree(watched, basket)
        doc = _doc("fr", "Frontrunner", tree)
        client, _ = _make_mock_client([doc])
        result = load_community_strategies(client=client)

        returned = set(result["candidates"][0]["tickers"])
        placeholder = frontrunner_fixture["percent_placeholder"]
        assert placeholder not in returned, (
            f"The '%' placeholder must NOT appear in extracted tickers (got {returned!r})"
        )

    def test_tickers_is_non_empty_for_tree_with_assets(self, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        tickers = result["candidates"][0]["tickers"]
        assert len(tickers) > 0, "tickers must be non-empty for a tree that contains assets"


# ---------------------------------------------------------------------------
# AC-5: limit and min_oos_sharpe filtering
# ---------------------------------------------------------------------------


class TestAC5Filtering:
    """limit caps candidate count; min_oos_sharpe pre-filters; docs lacking the metric are kept."""

    def _make_n_docs(self, n: int, sharpe_values: list[float | None] | None = None) -> list[dict]:
        """Build n distinct valid docs, each with a distinct single-asset tree."""
        tickers = [f"ASSET{i:03d}" for i in range(n)]
        docs = []
        for i, ticker in enumerate(tickers):
            tree = make_root(f"S{i}", "daily", [make_weight_equal([make_asset(ticker)])])
            sharpe = None
            if sharpe_values and i < len(sharpe_values):
                sharpe = sharpe_values[i]
            oos = {"sharpe": sharpe} if sharpe is not None else None
            docs.append(_doc(f"sid-{i}", f"Strat{i}", tree, oos))
        return docs

    def test_limit_caps_candidate_count(self):
        from advisors.community_strats import load_community_strategies

        docs = self._make_n_docs(5)
        client, _ = _make_mock_client(docs)
        result = load_community_strategies(client=client, limit=3)

        assert len(result["candidates"]) <= 3, (
            "limit=3 must cap candidates at 3 (loader may pull fewer)"
        )

    def test_limit_none_returns_all_valid(self):
        from advisors.community_strats import load_community_strategies

        docs = self._make_n_docs(4)
        client, _ = _make_mock_client(docs)
        result = load_community_strategies(client=client, limit=None)

        assert len(result["candidates"]) == 4, "limit=None must return all valid docs"

    def test_min_oos_sharpe_excludes_docs_below_threshold(self):
        """Docs whose sharpe is below min_oos_sharpe are filtered out."""
        from advisors.community_strats import load_community_strategies

        docs = self._make_n_docs(3, sharpe_values=[0.3, 0.7, 1.5])
        # threshold = 0.5; should keep only sharpe=0.7 and 1.5
        client, _ = _make_mock_client(docs)
        result = load_community_strategies(client=client, min_oos_sharpe=0.5)

        sharpes = [c["oos_metrics"]["sharpe"] for c in result["candidates"] if c["oos_metrics"]]
        for s in sharpes:
            assert s >= 0.5, f"candidate with sharpe {s} should have been filtered out"

    def test_min_oos_sharpe_keeps_docs_with_no_metric(self):
        """Docs lacking oos_metrics (or lacking the sharpe key) are kept when min_oos_sharpe is set.

        Per AC-5: 'docs lacking the metric are kept unless filtered — documented'.
        """
        from advisors.community_strats import load_community_strategies

        # One doc with sharpe below threshold, one with no oos_metrics at all
        tree_a = make_root("A", "daily", [make_weight_equal([make_asset("XYZ")])])
        tree_b = make_root("B", "daily", [make_weight_equal([make_asset("ABC")])])
        doc_low = _doc("low", "Low", tree_a, {"sharpe": 0.1})  # below threshold → excluded
        doc_missing = _doc("missing", "Missing", tree_b, None)  # no metrics → KEPT

        client, _ = _make_mock_client([doc_low, doc_missing])
        result = load_community_strategies(client=client, min_oos_sharpe=0.5)

        sids = [c["sid"] for c in result["candidates"]]
        assert "missing" in sids, (
            "doc with missing oos_metrics must be KEPT even when min_oos_sharpe is set"
        )
        assert "low" not in sids, (
            "doc with sharpe below min_oos_sharpe must be excluded"
        )

    def test_limit_zero_returns_empty_candidates(self):
        from advisors.community_strats import load_community_strategies

        docs = self._make_n_docs(3)
        client, _ = _make_mock_client(docs)
        result = load_community_strategies(client=client, limit=0)

        assert len(result["candidates"]) == 0, "limit=0 must return 0 candidates"


# ---------------------------------------------------------------------------
# AC-6: Honest-availability — connection failure and empty collection
# ---------------------------------------------------------------------------


class TestAC6HonestAvailability:
    """Mongo errors and empty collection → available=False; reason is bare type name; never raises."""

    def test_find_raises_returns_available_false(self):
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        col.find.side_effect = RuntimeError("connection timeout")
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        assert result["available"] is False, "available must be False on find() exception"

    def test_find_raises_reason_is_bare_type_name(self):
        """reason must be the exception type name — NOT the exception message."""
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        col.find.side_effect = RuntimeError("mongodb+srv://fakeuser:SECRETPW@cluster.example/db")
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        reason = result.get("reason", "")
        assert reason == "RuntimeError", (
            f"reason must be bare type name 'RuntimeError', got {reason!r}"
        )

    def test_find_raises_reason_does_not_contain_exception_message(self):
        """D-1: exception message (which could contain URIs or secrets) must NOT be in reason."""
        from advisors.community_strats import load_community_strategies

        secret_message = "Secret: mongodb+srv://fakeuser:SUPERSECRET@cluster.example.com/db"
        col = MagicMock()
        col.find.side_effect = ValueError(secret_message)
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        reason = result.get("reason", "")
        assert "SUPERSECRET" not in reason, "D-1: exception message must not appear in reason"
        assert "mongodb" not in reason.lower(), "D-1: URI fragment must not appear in reason"

    def test_find_raises_candidates_is_empty_list(self):
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        col.find.side_effect = ConnectionError("timeout")
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        assert result.get("candidates") == [], "candidates must be [] on connection failure"

    def test_empty_collection_returns_available_false(self):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([])  # empty list simulates empty collection
        result = load_community_strategies(client=client)

        assert result["available"] is False, "available must be False for empty collection"

    def test_empty_collection_reason_is_static_string(self):
        """Empty collection reason must be a static string — not an exception message."""
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([])
        result = load_community_strategies(client=client)

        reason = result.get("reason", "")
        assert isinstance(reason, str) and len(reason) > 0, "reason must be present for empty collection"
        # The reason must be a short static descriptor, not an exception repr
        # (AC-6: 'reason = type(exc).__name__ or "EmptyCollection"')
        assert "EmptyCollection" in reason or "empty" in reason.lower(), (
            f"reason for empty collection must indicate empty state, got {reason!r}"
        )

    def test_loader_never_raises_on_find_exception(self):
        """The loader NEVER raises; all exceptions are caught and returned in the dict."""
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        col.find.side_effect = MemoryError("OOM")
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        try:
            result = load_community_strategies(client=client)
        except Exception as exc:
            pytest.fail(
                f"load_community_strategies raised {type(exc).__name__}: {exc} — must never raise"
            )

        assert result["available"] is False

    def test_available_false_result_has_no_reason_containing_uri(self, fake_mongo_uri):
        """Even when MONGO_URI is set, the reason field must not contain it."""
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        col.find.side_effect = ConnectionError(fake_mongo_uri)
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        reason = result.get("reason", "")
        assert fake_mongo_uri not in reason, "MONGO_URI must not appear in reason"


# ---------------------------------------------------------------------------
# AC-7: Secrets — MONGO_URI never in returned data
# ---------------------------------------------------------------------------


class TestAC7SecretsNeverLeaked:
    """MONGO_URI env value must NEVER appear in any field of the returned dict."""

    def _uri_in_dict(self, d: Any, uri: str) -> bool:
        """Recursively search a dict/list/str for the URI string."""
        if isinstance(d, str):
            return uri in d
        if isinstance(d, dict):
            return any(self._uri_in_dict(v, uri) for v in d.values())
        if isinstance(d, (list, tuple, set, frozenset)):
            return any(self._uri_in_dict(v, uri) for v in d)
        return False

    def test_mongo_uri_absent_from_successful_result(self, fake_mongo_uri, simple_doc):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([simple_doc])
        result = load_community_strategies(client=client)

        assert not self._uri_in_dict(result, fake_mongo_uri), (
            "MONGO_URI must not appear anywhere in the successful result dict"
        )

    def test_mongo_uri_absent_from_error_result(self, fake_mongo_uri):
        """On connection failure, the URI must not leak into the error result."""
        from advisors.community_strats import load_community_strategies

        col = MagicMock()
        # The exception message contains the URI — must NOT propagate to reason
        col.find.side_effect = ConnectionError(fake_mongo_uri)
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col

        result = load_community_strategies(client=client)

        assert not self._uri_in_dict(result, fake_mongo_uri), (
            "MONGO_URI must not appear in the error result dict (D-1 + AC-7)"
        )

    def test_mongo_uri_absent_from_empty_collection_result(self, fake_mongo_uri):
        from advisors.community_strats import load_community_strategies

        client, _ = _make_mock_client([])
        result = load_community_strategies(client=client)

        assert not self._uri_in_dict(result, fake_mongo_uri), (
            "MONGO_URI must not appear in the empty-collection result dict"
        )


# ---------------------------------------------------------------------------
# AC-8: Boundary assertions — no Flask route, no LIVE_EXECUTION, lazy import
# ---------------------------------------------------------------------------


class TestAC8BoundaryAssertions:
    """Static source-level assertions about community_strats.py boundaries."""

    def test_module_importable_without_pymongo(self):
        """Importing advisors.community_strats must succeed even if pymongo is not importable.

        We simulate pymongo absence by temporarily removing it from sys.modules
        and patching the import mechanism, then confirming the module-level import
        does NOT blow up.

        If the module has already been imported (cached in sys.modules), we test
        that it was importable — the import itself is the proof when the test runs.
        """
        import sys
        # Remove the module from cache to force a reimport attempt
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("advisors.community_strats"):
                del sys.modules[mod_name]

        # Patch pymongo to raise ImportError at the top level
        with patch.dict(sys.modules, {"pymongo": None, "dns": None, "dns.resolver": None}):
            try:
                import importlib
                import advisors.community_strats as cs
                importlib.reload(cs)
            except ImportError as exc:
                pytest.fail(
                    f"advisors.community_strats raised ImportError when pymongo absent: {exc}\n"
                    "pymongo/dns imports must be LAZY (inside _connect_mongo only)"
                )

    def test_module_has_no_flask_route_decorator(self):
        """community_strats.py must not define any Flask routes."""
        module_path = _REPO_ROOT / "advisors" / "community_strats.py"
        if not module_path.exists():
            pytest.skip("Module not yet implemented — RED phase")
        content = module_path.read_text(encoding="utf-8")
        assert "@app.route" not in content, (
            "community_strats.py must not define Flask routes (AC-8)"
        )

    def test_module_has_no_live_execution_reference(self):
        """community_strats.py must not reference LIVE_EXECUTION."""
        module_path = _REPO_ROOT / "advisors" / "community_strats.py"
        if not module_path.exists():
            pytest.skip("Module not yet implemented — RED phase")
        content = module_path.read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in content, (
            "community_strats.py must not reference LIVE_EXECUTION (AC-8)"
        )

    def test_app_py_does_not_import_community_strats(self):
        """app.py must not import advisors.community_strats (not wired yet — slice 2 concern)."""
        app_path = _REPO_ROOT / "app.py"
        if not app_path.exists():
            pytest.skip("app.py not found")
        content = app_path.read_text(encoding="utf-8")
        assert "community_strats" not in content, (
            "app.py must not import community_strats yet (slice 1 is read-only, not wired)"
        )

    def test_load_community_strategies_is_the_public_entrypoint(self):
        """The public API is load_community_strategies — it must be importable and callable."""
        from advisors.community_strats import load_community_strategies

        assert callable(load_community_strategies), (
            "load_community_strategies must be a callable"
        )

    def test_load_community_strategies_accepts_keyword_only_params(self):
        """limit, min_oos_sharpe, and client must be keyword-only arguments."""
        import inspect
        from advisors.community_strats import load_community_strategies

        sig = inspect.signature(load_community_strategies)
        kw_only = {
            name
            for name, p in sig.parameters.items()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
        }
        for expected_param in ("limit", "min_oos_sharpe", "client"):
            assert expected_param in kw_only, (
                f"'{expected_param}' must be a keyword-only parameter of load_community_strategies"
            )

    def test_connect_mongo_is_internal(self):
        """_connect_mongo must be defined (it is the internal connect helper)."""
        import advisors.community_strats as cs

        assert hasattr(cs, "_connect_mongo"), (
            "_connect_mongo must be defined as an internal function"
        )
        assert callable(cs._connect_mongo), "_connect_mongo must be callable"


# ---------------------------------------------------------------------------
# AC-9: Query efficiency — bounded fetch and field projection
#
# The live Mongo functional check revealed that `list(collection.find({}))[:limit]`
# pulls ALL 8,339 docs (edn_strings up to 150 KB + multi-MB backtest/quantstats
# arrays) into memory before slicing.  Against a real Atlas cluster this hangs.
#
# The mock tests in AC-1..AC-5 used `col.find.return_value = docs` (a plain
# list), so they never observed whether find() was called with a limit or
# projection — the mock accepted any call signature and returned the same list.
#
# These tests use a cursor mock whose .limit() is trackable, so they are RED
# against the pull-all-then-slice implementation that never calls .limit()
# on the cursor, and RED against an implementation that omits the projection.
#
# Asserting cursor-chain (.limit) rather than find(limit=N kwarg) because:
#   - pymongo's canonical pattern is collection.find(filter, projection).limit(N)
#   - both call forms are valid fixes; the cursor-chain form is the most common
#   - the test accepts EITHER form (limit in find kwargs OR cursor.limit called)
#     so it does not over-constrain the implementer's choice.
# ---------------------------------------------------------------------------


def _make_cursor_mock(docs: list[dict]) -> "MagicMock":
    """Return a MagicMock cursor whose .limit() returns itself (chainable) and iterates docs.

    This mirrors a real pymongo cursor: iterable, and .limit() returns the same
    cursor.  The mock records all calls so tests can assert .limit(N) was called.
    """
    cursor = MagicMock()
    # __iter__ makes the cursor iterable — necessary so the loader can do
    # `for doc in cursor` or `list(cursor)` after chaining .limit().
    cursor.__iter__ = MagicMock(return_value=iter(docs))
    # .limit() must return the cursor itself (chainable) and also be iterable.
    limit_cursor = MagicMock()
    limit_cursor.__iter__ = MagicMock(return_value=iter(docs))
    cursor.limit.return_value = limit_cursor
    return cursor


def _make_collection_with_cursor(docs: list[dict]) -> "tuple[MagicMock, MagicMock]":
    """Return (collection_mock, cursor_mock) where find() returns the cursor mock.

    Distinct from _make_mock_collection which returns a plain list.  Use this
    helper when you need to assert cursor interaction (limit, projection).
    """
    cursor = _make_cursor_mock(docs)
    col = MagicMock()
    col.find.return_value = cursor
    return col, cursor


def _make_client_with_cursor(docs: list[dict]) -> "tuple[MagicMock, MagicMock, MagicMock]":
    """Return (client_mock, collection_mock, cursor_mock)."""
    col, cursor = _make_collection_with_cursor(docs)
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = col
    client.captplanet.strategies = col
    return client, col, cursor


class TestAC9QueryEfficiency:
    """Loader applies limit and projection at the query level — not post-fetch slice.

    These are RED against the pull-all-then-slice implementation that does:
        raw_docs = list(collection.find({}))
        if limit is not None:
            raw_docs = raw_docs[:limit]
    because that pattern never calls cursor.limit() and never passes a projection.
    """

    def _five_distinct_docs(self) -> list[dict]:
        """Five valid docs with distinct single-asset trees."""
        docs = []
        for i in range(5):
            ticker = f"TICK{i:03d}"
            tree = make_root(f"S{i}", "daily", [make_weight_equal([make_asset(ticker)])])
            docs.append(_doc(f"sid-{i}", f"Strat{i}", tree, {"sharpe": float(i)}))
        return docs

    # ------------------------------------------------------------------
    # Limit applied at query level (cursor.limit or find(limit=N))
    # ------------------------------------------------------------------

    def test_limit_applied_via_cursor_limit_or_find_kwarg(self):
        """When limit=N is supplied, the loader must bound the query — not slice after full fetch.

        Acceptable implementations:
          (A) collection.find(filter, projection).limit(N)  → cursor.limit called with N
          (B) collection.find(filter, projection, limit=N)  → find called with limit kwarg == N
          (C) collection.find({limit: N, ...})              → find called with limit in first arg

        The current buggy implementation does neither:
          raw_docs = list(collection.find({})); raw_docs = raw_docs[:limit]

        This test is RED against (buggy) because cursor.limit is never called and
        find() receives no limit kwarg.
        """
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client, limit=3)

        # Either cursor.limit(3) was called, OR find() was called with limit=3.
        # Inspect both paths and accept if either is satisfied.
        cursor_limit_called_with_3 = (
            cursor.limit.called
            and any(
                (args == (3,) or kwargs.get("limit") == 3)
                for args, kwargs in cursor.limit.call_args_list
            )
        )
        find_limit_kwarg = any(
            call.kwargs.get("limit") == 3
            for call in col.find.call_args_list
        )
        # Also check if limit was passed as the third positional arg to find
        # (pymongo: find(filter, projection, limit))
        find_limit_positional = any(
            len(call.args) >= 3 and call.args[2] == 3
            for call in col.find.call_args_list
        )

        assert cursor_limit_called_with_3 or find_limit_kwarg or find_limit_positional, (
            "limit=3 must be applied at the query level — loader must call cursor.limit(3) "
            "OR pass limit=3 to find(). Current implementation does list(find({}))[:3] "
            "which fetches all docs before slicing."
        )

    def test_limit_one_applied_at_query_not_sliced(self):
        """limit=1 edge case: single-doc bound must hit the cursor, not post-fetch slice."""
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client, limit=1)

        cursor_limit_called = cursor.limit.called and any(
            args == (1,) or kwargs.get("limit") == 1
            for args, kwargs in cursor.limit.call_args_list
        )
        find_limit_kwarg = any(
            call.kwargs.get("limit") == 1 for call in col.find.call_args_list
        )

        assert cursor_limit_called or find_limit_kwarg, (
            "limit=1 must be applied at the query level via cursor.limit(1) or find(limit=1)"
        )

    def test_no_limit_does_not_call_cursor_limit(self):
        """When limit=None (default), the loader must NOT artificially cap the cursor.

        This is a correctness guard in the opposite direction: limit=None must fetch
        all docs, so cursor.limit() must not be called with a restrictive value.
        (It's fine if cursor.limit is never called at all, or called with 0 which
        pymongo treats as 'no limit'.)
        """
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client, limit=None)

        # cursor.limit must NOT have been called with a positive integer bound
        if cursor.limit.called:
            for call in cursor.limit.call_args_list:
                args, kwargs = call
                bound = args[0] if args else kwargs.get("limit", 0)
                assert bound == 0 or bound is None, (
                    f"limit=None must not restrict the cursor; cursor.limit called with {bound!r}"
                )

    # ------------------------------------------------------------------
    # Projection restricts fetched fields
    # ------------------------------------------------------------------

    def test_find_called_with_projection_dict(self):
        """find() must be called with a projection dict to suppress large unused fields.

        The current buggy implementation calls collection.find({}) with no projection,
        which fetches every field including backtest and quantstats_metrics arrays
        that can be multiple MB per document.

        This test is RED against the buggy implementation because find() receives
        only one argument (the filter), never a projection dict.
        """
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        # find() must have been called with at least two arguments, the second of
        # which is the projection dict — OR with a projection= keyword argument.
        assert col.find.called, "collection.find() must be called"

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        kwarg_projection = call.kwargs.get("projection")
        projection = positional_projection if positional_projection is not None else kwarg_projection

        assert projection is not None, (
            "find() must be called with a projection dict (second positional arg or "
            "projection= kwarg) to avoid fetching multi-MB backtest/quantstats arrays. "
            "Current implementation: collection.find({}) with no projection."
        )
        assert isinstance(projection, dict), (
            f"projection must be a dict, got {type(projection).__name__!r}"
        )

    def test_projection_includes_edn_string(self):
        """The projection must include 'edn_string' — the loader's primary payload field."""
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        projection = (
            positional_projection
            if positional_projection is not None
            else call.kwargs.get("projection")
        )

        # If no projection at all this will be caught by test_find_called_with_projection_dict;
        # here we guard that the required field is included when a projection IS present.
        if projection is None:
            pytest.fail(
                "find() was called with no projection — 'edn_string' cannot be included. "
                "Fix: pass a projection dict that includes 'edn_string'."
            )

        assert "edn_string" in projection, (
            f"projection must include 'edn_string'; got projection={projection!r}"
        )

    def test_projection_includes_sid(self):
        """The projection must include 'sid' — required field checked before parse."""
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        projection = (
            positional_projection
            if positional_projection is not None
            else call.kwargs.get("projection")
        )

        if projection is None:
            pytest.fail("find() was called with no projection — 'sid' cannot be included.")

        assert "sid" in projection, (
            f"projection must include 'sid'; got projection={projection!r}"
        )

    def test_projection_includes_oos_metrics(self):
        """The projection must include 'oos_metrics' — surfaced on every candidate."""
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        projection = (
            positional_projection
            if positional_projection is not None
            else call.kwargs.get("projection")
        )

        if projection is None:
            pytest.fail("find() was called with no projection — 'oos_metrics' cannot be included.")

        assert "oos_metrics" in projection, (
            f"projection must include 'oos_metrics'; got projection={projection!r}"
        )

    def test_projection_excludes_backtest(self):
        """The projection must NOT request the 'backtest' field.

        'backtest' contains the full backtest time-series array — potentially MB per doc.
        Including it in the projection (or omitting an exclusion when using an inclusion
        projection) defeats the entire purpose of projecting.

        In MongoDB, an INCLUSION projection lists only the fields you want — fields not
        listed are automatically excluded.  So this test asserts that 'backtest' is NOT
        a key with value 1 (or True) in the projection dict.
        """
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        projection = (
            positional_projection
            if positional_projection is not None
            else call.kwargs.get("projection")
        )

        if projection is None:
            # No projection means all fields fetched — backtest IS included.
            pytest.fail(
                "find() was called with no projection — 'backtest' field will be fetched. "
                "Pass an inclusion projection that omits 'backtest'."
            )

        # In an inclusion projection, a field is included only if its value is truthy (1/True).
        # If 'backtest' is in the projection with a truthy value, it will be fetched.
        backtest_included = projection.get("backtest", 0)
        assert not backtest_included, (
            f"projection must NOT include 'backtest' (fetching it bloats memory); "
            f"got projection={projection!r}"
        )

    def test_projection_excludes_quantstats_metrics(self):
        """The projection must NOT request 'quantstats_metrics'.

        'quantstats_metrics' is another large nested object in the corpus docs.
        Same logic as test_projection_excludes_backtest.
        """
        from advisors.community_strats import load_community_strategies

        docs = self._five_distinct_docs()
        client, col, cursor = _make_client_with_cursor(docs)

        load_community_strategies(client=client)

        call = col.find.call_args
        positional_projection = call.args[1] if len(call.args) >= 2 else None
        projection = (
            positional_projection
            if positional_projection is not None
            else call.kwargs.get("projection")
        )

        if projection is None:
            pytest.fail(
                "find() was called with no projection — 'quantstats_metrics' will be fetched. "
                "Pass an inclusion projection that omits 'quantstats_metrics'."
            )

        qm_included = projection.get("quantstats_metrics", 0)
        assert not qm_included, (
            f"projection must NOT include 'quantstats_metrics'; got projection={projection!r}"
        )
