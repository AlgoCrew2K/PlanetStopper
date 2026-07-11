"""RED tests — Atlas sharpe field-path correctness for advisors/community_strats.py.

THE DEFECT (PM-verified via the live-Atlas gate + direct Mongo reads,
DE-ATLAS-SHARPE-FIELD-001, amendment to DE-ATLAS-SLOW-QUERY-001):
  Ranking, the min_oos_sharpe filter, and the dedup tie-break all read
  oos_metrics['sharpe'] (lowercase) — this field exists on 0 of 11,227 live
  captplanet.strategies docs. The REAL field is oos_metrics['Sharpe'] (capital S),
  STRING-valued (e.g. "9.45"), present on 10,067/11,227 docs. So "top-N by sharpe"
  has always selected an ARBITRARY N docs (not actually ranked), and min_oos_sharpe
  has always been a no-op.

THE FIX (prescribed): read oos_metrics['Sharpe'], parse the string DEFENSIVELY to
  float (missing / non-numeric / percent-formatted / "nan" / "inf" -> treated as
  unparseable, sorts to the BOTTOM, never raises — "nan"/"inf" pass Python's bare
  float() but are not valid Sharpe ratios) at all 3 consumption sites: client-side
  ranking (the fetch's selection step), the min_oos_sharpe filter, and the dedup
  tie-break (_oos_sharpe()).

ADVERSARIAL FOCUS:
  - TestRealSharpeFieldPath: each test is constructed so an implementation still
    reading the OLD lowercase field (which sees every doc as sharpe-missing)
    produces a DEMONSTRABLY WRONG answer vs the one produced by correctly reading
    the real field — not just "field absent, degrades gracefully".
  - TestDefensiveSharpeParsing: missing / non-numeric / percent-formatted / "nan" /
    "inf" values must never raise and must always sort to the bottom (never treated
    as a valid, let alone a *high*, sharpe). Malformed docs are placed FIRST in
    fixture insertion order in both tests so a naive unranked/insertion-order
    selection is provably wrong, not accidentally right.

This file stays focused on FIELD-PATH / PARSING correctness, independent of query
STRUCTURE (covered by test_community_strats_fetch_query.py) — its _FakeCollection
deliberately does NOT simulate server-side sorting at all, so these tests exercise
the CLIENT-SIDE ranking logic regardless of whatever sort kwarg (if any) an
implementation passes to find().

Fetch seam matches test_community_strats_fetch_query.py:
  Layer 1 — patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn())
  Layer 2 — patch("pymongo.MongoClient", return_value=<fake client wrapping _FakeCollection>)

No live network calls. No production DB writes. No @pytest.mark.live.
"""

from __future__ import annotations

import ast
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from advisors import symphony_schema as ss

# ---------------------------------------------------------------------------
# In-memory Mongo double (no server-side sort simulation — see module docstring)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor double: supports .limit(n) chaining and iteration."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    """In-memory Mongo collection double. Supported filter shapes: {} (match all)
    and {'_id': {'$in': [...]}}. Supported projection shapes: top-level inclusion
    and one-level dotted inclusion (e.g. 'oos_metrics.Sharpe'). Deliberately has NO
    server-side sort support — any sort kwarg passed to find() is recorded but
    ignored, so a passing test proves the implementation ranks correctly on its
    own (client-side), not because the double happened to sort for it.
    """

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self.find_calls: list[dict] = []

    def find(self, filter=None, projection=None, *, sort=None, allow_disk_use=None):  # noqa: A002
        filter = filter or {}
        self.find_calls.append({"filter": filter, "projection": projection, "sort": sort})
        matched = self._match(filter)
        return _FakeCursor(self._projected(matched, projection))

    def _match(self, filt: dict) -> list[dict]:
        if not filt:
            return list(self._docs)
        id_clause = filt.get("_id")
        if isinstance(id_clause, dict) and "$in" in id_clause:
            wanted = set(id_clause["$in"])
            return [d for d in self._docs if d.get("_id") in wanted]
        return [d for d in self._docs if all(d.get(k) == v for k, v in filt.items())]

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
                if "." in key:
                    top, _, rest = key.partition(".")
                    sub = d.get(top)
                    if isinstance(sub, dict) and rest in sub:
                        proj.setdefault(top, {})[rest] = sub[rest]
                elif key in d:
                    proj[key] = d[key]
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
# Doc fixtures — REAL captplanet.strategies field shape
# ---------------------------------------------------------------------------


def _make_minimal_tree(ticker: str) -> dict:
    """Return a minimal validate_tree==[] tree via symphony_schema constructors."""
    asset = ss.make_asset(ticker)
    weight_node = ss.make_weight_equal([asset])
    return ss.make_root(f"Test-{ticker}", "daily", [weight_node])


def _make_real_shape_doc(doc_id: str, sid: str, ticker: str, sharpe_raw: str | None) -> dict:
    """Return a doc using the REAL captplanet.strategies oos_metrics shape: capital-S
    'Sharpe', string-valued. sharpe_raw is placed verbatim as the string value (the
    key is omitted entirely when sharpe_raw is None) so callers can exercise
    malformed values directly (e.g. "N/A", "12.3%", "nan", "inf").
    """
    tree = _make_minimal_tree(ticker)
    oos_metrics = {"Sharpe": sharpe_raw} if sharpe_raw is not None else {}
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
    atlas_cache.cached_pull entirely, matching the seam used by the sibling files.
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


# ---------------------------------------------------------------------------
# TestRealSharpeFieldPath — lowercase-reading impl produces a WRONG answer
# ---------------------------------------------------------------------------


class TestRealSharpeFieldPath:
    """Each test is built so a lowercase-reading implementation (which sees 100% of
    docs as sharpe-missing, since the real field is capital-S) produces a
    demonstrably DIFFERENT, WRONG answer than one reading the real field.
    """

    def test_ranking_selects_the_genuinely_highest_sharpe_docs(self, mod, monkeypatch):
        """With cap < total docs, the selected top-N must be the N docs with the
        highest REAL (capital-S, string) sharpe — not an arbitrary/insertion-order
        subset, which is what a lowercase-reading implementation produces (every
        doc looks sharpe-missing to it, so selection degrades to insertion order).
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        cap = 3
        monkeypatch.setattr(mod, "_MAX_FETCH_DOCS", cap)

        # Deliberately built so insertion order != sharpe-descending order, so an
        # arbitrary/unranked selection provably differs from the correct one.
        specs = [
            ("s-1", "TLT", "0.10"),
            ("s-2", "SPY", "2.50"),
            ("s-3", "QQQ", "1.80"),
            ("s-4", "GLD", "0.90"),
            ("s-5", "IVV", "3.00"),
        ]
        docs = [
            _make_real_shape_doc(f"id-{sid}", sid, ticker, sharpe_str)
            for sid, ticker, sharpe_str in specs
        ]
        collection = _FakeCollection(docs)

        result = _run_live_fetch(mod, collection)

        assert result["available"] is True, f"expected available=True; got {result!r}"
        expected_top_n_sids = {
            sid for sid, _t, s in sorted(specs, key=lambda row: float(row[2]), reverse=True)[:cap]
        }
        pulled_sids = {c["sid"] for c in result["candidates"]}
        assert pulled_sids == expected_top_n_sids, (
            f"expected the top-{cap}-by-Sharpe set {expected_top_n_sids!r}; got "
            f"{pulled_sids!r}. A lowercase-field-reading implementation would see every "
            "doc as sharpe-missing and select an arbitrary (insertion-order) subset "
            "instead — s-1 (0.10) here instead of s-5 (3.00)."
        )

    def test_min_oos_sharpe_filter_actually_excludes_low_sharpe_docs(self, mod, monkeypatch):
        """min_oos_sharpe must genuinely exclude docs below the floor. Against the
        real field shape, a lowercase-reading implementation sees every doc as
        sharpe-missing (missing sharpe is always KEPT), so the floor becomes a
        total no-op — this test proves it isn't.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        doc_above = _make_real_shape_doc("id-above", "above", "SPY", "1.50")
        doc_below = _make_real_shape_doc("id-below", "below", "TLT", "0.30")
        collection = _FakeCollection([doc_above, doc_below])

        result = _run_live_fetch(mod, collection, min_oos_sharpe=1.0)

        assert result["available"] is True
        sids = {c["sid"] for c in result["candidates"]}
        assert "above" in sids, "doc above the floor must be included"
        assert "below" not in sids, (
            "doc below the floor must be excluded — a lowercase-field-reading "
            "implementation would keep it (a doc it sees as sharpe-missing is never "
            "filtered by min_oos_sharpe)"
        )
        assert result["stats"]["sharpe_filtered"] >= 1, (
            "stats['sharpe_filtered'] must reflect the real exclusion"
        )

    def test_dedup_keeps_the_genuinely_higher_sharpe_duplicate(self, mod, monkeypatch):
        """Two docs with identical composition (same ticker) but different REAL
        sharpe values must dedup to the higher one. A lowercase-reading
        implementation sees both as sharpe-missing (tied at -inf), so its "winner"
        is whichever was encountered first — here the lower-sharpe doc, since it's
        inserted first — not reliably the higher-sharpe one.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        low = _make_real_shape_doc("id-low-dup", "sid-low-dup", "IVV", "0.50")
        high = _make_real_shape_doc("id-high-dup", "sid-high-dup", "IVV", "1.80")
        collection = _FakeCollection([low, high])

        result = _run_live_fetch(mod, collection)

        assert result["available"] is True
        assert len(result["candidates"]) == 1, (
            f"expected exactly 1 candidate after dedup; got {len(result['candidates'])}"
        )
        assert result["candidates"][0]["sid"] == "sid-high-dup", (
            "dedup must keep the genuinely higher-Sharpe duplicate (1.80, sid-high-dup), "
            "not whichever happens to be encountered first when sharpe isn't read "
            f"correctly; got {result['candidates'][0]['sid']!r}"
        )


# ---------------------------------------------------------------------------
# TestDefensiveSharpeParsing — malformed values never raise, sort to bottom
# ---------------------------------------------------------------------------


class TestDefensiveSharpeParsing:
    """Parsing oos_metrics['Sharpe'] must be defensive — missing, non-numeric,
    percent-formatted, and 'nan'/'inf' strings must never raise and must always
    sort to the BOTTOM (never treated as a valid, let alone a *high*, sharpe).
    'nan'/'inf' pass Python's bare float() but are not valid Sharpe ratios.
    """

    def test_malformed_sharpe_variants_sort_to_bottom_never_raise(self, mod, monkeypatch):
        """2 valid numeric-string sharpes + 5 malformed variants (missing,
        non-numeric, percent-formatted, 'nan', 'inf'), cap=2: the top-2 selected
        must be exactly the 2 valid docs — none of the 5 malformed ones — and the
        call must not raise. Malformed docs are inserted FIRST so a naive
        insertion-order truncation is provably wrong, not accidentally right.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        cap = 2
        monkeypatch.setattr(mod, "_MAX_FETCH_DOCS", cap)

        docs = [
            _make_real_shape_doc("id-missing", "missing", "TLT", None),
            _make_real_shape_doc("id-nonnumeric", "nonnumeric", "GLD", "N/A"),
            _make_real_shape_doc("id-percent", "percent", "IVV", "12.3%"),
            _make_real_shape_doc("id-nan", "nan-val", "AGG", "nan"),
            _make_real_shape_doc("id-inf", "inf-val", "EFA", "inf"),
            _make_real_shape_doc("id-valid-hi", "valid-hi", "SPY", "9.45"),
            _make_real_shape_doc("id-valid-lo", "valid-lo", "QQQ", "3.20"),
        ]
        collection = _FakeCollection(docs)

        try:
            result = _run_live_fetch(mod, collection)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"load_community_strategies raised {type(exc).__name__}: {exc} — "
                "defensive sharpe parsing must never raise"
            )

        assert result["available"] is True, f"expected available=True; got {result!r}"
        sids = {c["sid"] for c in result["candidates"]}
        assert sids == {"valid-hi", "valid-lo"}, (
            f"expected exactly the 2 valid-sharpe docs to survive the cap={cap} "
            f"selection; got {sids!r}. Every malformed variant (missing, non-numeric, "
            "percent-formatted, 'nan', 'inf') must sort to the bottom, never the top — "
            "a naive insertion-order truncation would (wrongly) keep the 5 malformed "
            "docs instead, since they're listed first."
        )

    def test_valid_numeric_string_sharpe_ranks_above_valid_lower_one(self, mod, monkeypatch):
        """Boundary case (cap=1): a well-formed numeric string like '9.45' must rank
        ABOVE a lower one like '3.20'. The lower one is inserted FIRST so a naive
        (unranked) selection would (wrongly) keep it instead.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        monkeypatch.setattr(mod, "_MAX_FETCH_DOCS", 1)
        low = _make_real_shape_doc("id-lo", "lo", "QQQ", "3.20")
        high = _make_real_shape_doc("id-hi", "hi", "SPY", "9.45")
        collection = _FakeCollection([low, high])

        result = _run_live_fetch(mod, collection)

        assert result["available"] is True
        sids = {c["sid"] for c in result["candidates"]}
        assert sids == {"hi"}, (
            "cap=1 must select the doc with the higher parsed Sharpe ('hi'=9.45 > "
            f"'lo'=3.20), not an arbitrary/insertion-order pick; got {sids!r}"
        )


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
        "importlib.reload(...) found in test_community_strats_sharpe_field.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. Patch the relevant "
        "seam (atlas_cache.cached_pull / pymongo.MongoClient) directly instead of reloading."
    )
