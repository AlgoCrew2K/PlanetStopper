"""
RED tests for symphony_logic.py — Cycle P2.

Tier 1: fixture-based unit tests (no network, every CI pass).

The fixtures under tests/fixtures/symphony_logic/ are REAL captured-from-producer
`/score` responses. Every expected value in this file is DERIVED from those
fixtures at test time by an independent reference walk — nothing about the
decision tree is hardcoded. If Composer's payloads change, regenerate the
fixtures and these tests re-derive automatically.

The module under test (symphony_logic.py) does NOT exist yet — these tests are
RED by construction (ImportError at collection or call time).
"""

import json
import pathlib
from collections import Counter

import pytest

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

SYMPHONY_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "symphony_logic"


def _load_fixture(name: str) -> dict:
    with open(SYMPHONY_FIXTURE_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def raw_small() -> dict:
    """Real /score response — 'Corporate Chaos 5 ways' (~132 KB)."""
    return _load_fixture("sample_score_small.json")


@pytest.fixture(scope="module")
def raw_large() -> dict:
    """Real /score response — 'Planet of Hunted Cascades' (~1 MB)."""
    return _load_fixture("sample_score_large.json")


# ---------------------------------------------------------------------------
# Independent reference walk — the test's own "source of truth" derived
# straight from the fixture. The implementation must AGREE with this; the
# test never trusts the implementation to define correctness.
#
# IMPORTANT: this reference counts indicator functions in BOTH places they
# appear in real payloads:
#   1. the flat `lhs-fn` / `rhs-fn` keys on `if-child` nodes
#   2. nested inside `condition.conditions[].lhs.fn` / `.rhs.fn` (compound
#      conditions) — confirmed present in sample_score_small.json
# A naive walk that only reads the flat keys UNDERCOUNTS indicator usage.
# ---------------------------------------------------------------------------


def _reference_census(raw: dict) -> dict:
    steps: Counter = Counter()
    indicators: Counter = Counter()
    tickers: set[str] = set()
    group_labels: list[str] = []
    max_depth = 0
    total_nodes = 0
    max_breadth = 0

    def _scan_condition(cond):
        """Pull indicator fns out of a compound/binary `condition` block."""
        if not isinstance(cond, dict):
            return
        for side in ("lhs", "rhs"):
            operand = cond.get(side)
            if isinstance(operand, dict) and operand.get("fn"):
                indicators[operand["fn"]] += 1
        for sub in cond.get("conditions", []) or []:
            _scan_condition(sub)

    def walk(node, depth):
        nonlocal max_depth, total_nodes, max_breadth
        if not isinstance(node, dict):
            return
        total_nodes += 1
        max_depth = max(max_depth, depth)

        step = node.get("step")
        if step:
            steps[step] += 1

        for fn_key in ("lhs-fn", "rhs-fn"):
            if node.get(fn_key):
                indicators[node[fn_key]] += 1

        if isinstance(node.get("condition"), dict):
            _scan_condition(node["condition"])

        if node.get("ticker"):
            tickers.add(node["ticker"])

        if step == "group" and node.get("name"):
            group_labels.append(node["name"])

        children = node.get("children", []) or []
        max_breadth = max(max_breadth, len(children))
        for child in children:
            walk(child, depth + 1)

    walk(raw, 0)
    return {
        "steps": dict(steps),
        "indicators": dict(indicators),
        "tickers": tickers,
        "group_labels": group_labels,
        "max_depth": max_depth,
        "total_nodes": total_nodes,
        "max_breadth": max_breadth,
    }


# Expected top-level keys of the condensed summary (the proposed contract).
_EXPECTED_SUMMARY_KEYS = {
    "name",
    "asset_universe",
    "indicators_used",
    "tree_depth",
    "tree_breadth",
    "total_nodes",
    "group_labels",
    "rebalance",
}


# ===========================================================================
# Tier 1.1 — condense_symphony_logic on the SMALL fixture
# ===========================================================================


def test_condense_small_returns_all_expected_summary_keys(raw_small):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_small)
    assert isinstance(summary, dict)
    # Every proposed key must be present. Extra keys are allowed (the GREEN
    # implementer may refine the contract upward) but none may be missing.
    assert _EXPECTED_SUMMARY_KEYS.issubset(summary.keys()), (
        f"missing keys: {_EXPECTED_SUMMARY_KEYS - set(summary.keys())}"
    )


def test_condense_small_name_matches_fixture(raw_small):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_small)
    # Derived from fixture — not hardcoded.
    assert summary["name"] == raw_small["name"]


def test_condense_small_rebalance_matches_fixture(raw_small):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_small)
    assert summary["rebalance"] == raw_small["rebalance"]


def test_condense_small_asset_universe_is_nonempty_ticker_strings(raw_small):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_small)
    universe = summary["asset_universe"]
    assert isinstance(universe, list)
    assert len(universe) > 0
    assert all(isinstance(t, str) and t for t in universe)
    # No duplicates — it's a *universe* of distinct tickers.
    assert len(universe) == len(set(universe))


def test_condense_small_asset_universe_matches_fixture_tickers(raw_small):
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_small)
    summary = condense_symphony_logic(raw_small)
    # The condensed universe must equal the distinct tickers actually present
    # in the real tree — derived, never hardcoded.
    assert set(summary["asset_universe"]) == ref["tickers"]


def test_condense_small_indicators_used_counts_are_positive_ints(raw_small):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_small)
    indicators = summary["indicators_used"]
    assert isinstance(indicators, dict)
    assert len(indicators) > 0
    for name, count in indicators.items():
        assert isinstance(name, str) and name
        assert isinstance(count, int) and not isinstance(count, bool)
        assert count > 0


def test_condense_small_tree_metrics_are_plausible_positive_ints(raw_small):
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_small)
    summary = condense_symphony_logic(raw_small)
    for key in ("tree_depth", "tree_breadth", "total_nodes"):
        val = summary[key]
        assert isinstance(val, int) and not isinstance(val, bool), key
        assert val > 0, key
    # total_nodes can't exceed what's actually in the tree, and the root
    # alone guarantees at least 1. Bound it against the reference walk.
    assert summary["total_nodes"] == ref["total_nodes"]
    assert summary["tree_depth"] == ref["max_depth"]
    assert summary["tree_breadth"] == ref["max_breadth"]


def test_condense_small_group_labels_match_fixture(raw_small):
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_small)
    summary = condense_symphony_logic(raw_small)
    labels = summary["group_labels"]
    assert isinstance(labels, list)
    assert all(isinstance(g, str) for g in labels)
    # Creator-authored group names are load-bearing semantic context for the
    # Claude advisor prompt — every distinct one in the tree must survive.
    assert set(labels) == set(ref["group_labels"])


# ===========================================================================
# Tier 1.2 — condense_symphony_logic on the LARGE fixture + size contract
# ===========================================================================


def test_condense_large_does_not_raise(raw_large):
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_large)
    assert isinstance(summary, dict)
    assert _EXPECTED_SUMMARY_KEYS.issubset(summary.keys())


def test_condense_large_output_is_prompt_sized(raw_large):
    """The whole point of condensation: a ~1 MB tree -> a small summary.

    8 KB ceiling rationale: the condensed dict is one of several context
    blocks in the Claude advisor prompt; it must be a *summary*, not the
    tree. 8192 bytes is generous headroom for name + ~92 tickers + ~7
    indicator counts + metrics + group labels, while still being two orders
    of magnitude smaller than the 1 MB raw payload. If a future symphony
    legitimately needs more, raise this deliberately — don't let it drift.
    """
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(raw_large)
    serialized = json.dumps(summary)
    raw_size = len(json.dumps(raw_large))
    assert len(serialized.encode("utf-8")) < 8192, (
        f"condensed summary is {len(serialized.encode('utf-8'))} bytes "
        f"(raw was {raw_size}) — not prompt-sized"
    )


def test_condense_large_universe_matches_fixture_tickers(raw_large):
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_large)
    summary = condense_symphony_logic(raw_large)
    assert set(summary["asset_universe"]) == ref["tickers"]
    assert len(summary["asset_universe"]) == len(ref["tickers"])


def test_condense_large_tree_metrics_match_reference(raw_large):
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_large)
    summary = condense_symphony_logic(raw_large)
    assert summary["total_nodes"] == ref["total_nodes"]
    assert summary["tree_depth"] == ref["max_depth"]
    assert summary["tree_breadth"] == ref["max_breadth"]


# ===========================================================================
# Tier 1.3 — ADVERSARIAL: does the summary preserve enough signal?
# ===========================================================================


def test_condense_preserves_dominant_indicator_signal(raw_large):
    """If the raw tree is heavy on RSI, indicators_used MUST reflect that.

    The reference census of both fixtures shows relative-strength-index is by
    far the most-used indicator function. A condensed summary that drops or
    flattens indicator usage would lose the single most important signal
    about *how* the symphony makes decisions.
    """
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_large)
    dominant_fn, dominant_count = max(ref["indicators"].items(), key=lambda kv: kv[1])
    summary = condense_symphony_logic(raw_large)
    indicators = summary["indicators_used"]
    assert dominant_fn in indicators, (
        f"dominant indicator {dominant_fn!r} missing from condensed summary"
    )
    # The dominant indicator in the raw tree must remain the dominant one in
    # the summary — relative ordering of signal must survive condensation.
    assert indicators[dominant_fn] == max(indicators.values())


def test_condense_indicator_counts_include_compound_condition_fns(raw_small):
    """ADVERSARIAL — undercounting trap.

    In sample_score_small.json, indicator functions appear in TWO places:
      * flat `lhs-fn` / `rhs-fn` keys on if-child nodes
      * nested inside `condition.conditions[].lhs.fn` (compound conditions)
    An implementation that only reads the flat keys will UNDERCOUNT. The
    reference census walks both; the implementation's counts must match it.
    If they don't, the summary misrepresents how indicator-driven the
    symphony actually is.
    """
    from symphony_logic import condense_symphony_logic

    ref = _reference_census(raw_small)
    summary = condense_symphony_logic(raw_small)
    assert summary["indicators_used"] == ref["indicators"], (
        "indicator counts disagree with full reference walk — likely the "
        "implementation ignores indicator fns nested in compound conditions"
    )


def test_condense_preserves_every_traded_ticker(raw_small):
    """If the symphony trades N specific tickers, asset_universe lists them all.

    No ticker that appears as an `asset` node anywhere in the tree may be
    dropped from the universe — Claude needs the complete traded set.
    """
    from symphony_logic import condense_symphony_logic

    # Independently collect only `step == "asset"` tickers.
    asset_tickers: set[str] = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("step") == "asset" and node.get("ticker"):
            asset_tickers.add(node["ticker"])
        for child in node.get("children", []) or []:
            walk(child)

    walk(raw_small)
    assert asset_tickers, "fixture sanity: expected asset nodes in small tree"

    summary = condense_symphony_logic(raw_small)
    missing = asset_tickers - set(summary["asset_universe"])
    assert not missing, f"asset_universe dropped traded tickers: {missing}"


def test_condense_small_and_large_have_distinct_signal(raw_small, raw_large):
    """Two different symphonies must NOT condense to look the same.

    Guards against a stub that returns a constant/empty-ish summary and
    happens to pass shape checks. The two real fixtures differ in name,
    universe size, depth, and node count — the summaries must differ too.
    """
    from symphony_logic import condense_symphony_logic

    s_small = condense_symphony_logic(raw_small)
    s_large = condense_symphony_logic(raw_large)
    assert s_small["name"] != s_large["name"]
    # Large tree ('Planet of Hunted Cascades', depth 230, 92 tickers) is
    # strictly bigger than small ('Corporate Chaos 5 ways', depth 19, 27).
    assert s_large["total_nodes"] > s_small["total_nodes"]
    assert s_large["tree_depth"] > s_small["tree_depth"]
    assert len(s_large["asset_universe"]) > len(s_small["asset_universe"])


# ===========================================================================
# Tier 1.4 — malformed / empty raw tree -> graceful handling
# ===========================================================================


def test_condense_empty_dict_returns_sensible_empty_summary():
    """Empty dict in -> empty-but-well-shaped summary out, no exception."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic({})
    assert isinstance(summary, dict)
    # Contract keys still present so downstream prompt assembly never KeyErrors.
    assert _EXPECTED_SUMMARY_KEYS.issubset(summary.keys())
    # Collection-typed fields are empty, not None.
    assert summary["asset_universe"] == []
    assert summary["indicators_used"] == {}
    assert summary["group_labels"] == []
    assert summary["total_nodes"] == 0


def test_condense_tree_with_no_children_key_does_not_raise():
    """A root node missing `children` entirely must not crash the walk."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic({"step": "root", "name": "Bare"})
    assert summary["name"] == "Bare"
    assert summary["asset_universe"] == []
    assert summary["total_nodes"] >= 1  # the root itself counts


def test_condense_tree_with_malformed_children_does_not_raise():
    """`children` containing junk (None, strings, ints) is tolerated."""
    from symphony_logic import condense_symphony_logic

    malformed = {
        "step": "root",
        "name": "Junk",
        "children": [None, "not-a-node", 42, {"step": "asset", "ticker": "SPY"}],
    }
    summary = condense_symphony_logic(malformed)
    # The one valid asset node is still picked up; junk is skipped silently.
    assert summary["asset_universe"] == ["SPY"]


def test_condense_missing_name_and_rebalance_uses_safe_defaults():
    """Absent optional scalars degrade to safe defaults, not exceptions."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic({"step": "root", "children": []})
    # name / rebalance must still be present and JSON-serializable scalars.
    assert "name" in summary and "rebalance" in summary
    json.dumps(summary)  # must not raise


# ===========================================================================
# Tier 1.5 — fetch_symphony_score: contract + auth reuse (mocked network)
# ===========================================================================


def test_fetch_symphony_score_calls_score_endpoint(monkeypatch):
    """fetch_symphony_score GETs /symphonies/{id}/score and returns the body."""
    import symphony_logic

    captured = {}
    fixture_body = _load_fixture("sample_score_small.json")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return fixture_body

        def raise_for_status(self):
            pass

    def _fake_get(url, headers=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(symphony_logic.requests, "get", _fake_get)

    result = symphony_logic.fetch_symphony_score("hvPiGP1O7AHfutHE3Fjy")

    assert result == fixture_body
    # Endpoint shape: must hit the canonical /score path for the given id.
    assert captured["url"].endswith("/symphonies/hvPiGP1O7AHfutHE3Fjy/score")
    assert "api.composer.trade" in captured["url"]


def test_fetch_symphony_score_reuses_composer_auth_headers(monkeypatch):
    """Auth must reuse the existing Composer header contract, not reinvent it.

    The header KEYS are the contract (x-api-key-id, authorization); the secret
    VALUES come from the environment and are never asserted as literals.
    """
    import symphony_logic

    captured = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {}

        def raise_for_status(self):
            pass

    def _fake_get(url, headers=None, timeout=None, **kwargs):
        captured["headers"] = headers or {}
        return _FakeResponse()

    monkeypatch.setattr(symphony_logic.requests, "get", _fake_get)
    symphony_logic.fetch_symphony_score("abc123")

    headers = captured["headers"]
    assert "x-api-key-id" in headers
    assert "authorization" in headers
    assert str(headers["authorization"]).lower().startswith("bearer ")


# ===========================================================================
# Tier 1.6 — get_condensed_logic: fetch + condense + cache
# ===========================================================================


def test_get_condensed_logic_returns_condensed_summary(monkeypatch):
    """get_condensed_logic wraps fetch + condense -> a condensed summary dict."""
    import symphony_logic

    fixture_body = _load_fixture("sample_score_small.json")
    monkeypatch.setattr(symphony_logic, "fetch_symphony_score", lambda sid: fixture_body)

    result = symphony_logic.get_condensed_logic("sym-1", use_cache=False)
    assert isinstance(result, dict)
    assert _EXPECTED_SUMMARY_KEYS.issubset(result.keys())
    # It returns the *condensed* form, not the raw tree.
    assert "children" not in result
    assert result["name"] == fixture_body["name"]


def test_get_condensed_logic_second_call_hits_cache(monkeypatch):
    """With use_cache=True, a repeated call must NOT re-fetch from the network."""
    import symphony_logic

    fixture_body = _load_fixture("sample_score_small.json")
    fetch_calls = {"n": 0}

    def _counting_fetch(sid):
        fetch_calls["n"] += 1
        return fixture_body

    monkeypatch.setattr(symphony_logic, "fetch_symphony_score", _counting_fetch)
    # Reset any module-level cache state so the test is order-independent.
    if hasattr(symphony_logic, "clear_cache"):
        symphony_logic.clear_cache()

    first = symphony_logic.get_condensed_logic("sym-cache", use_cache=True)
    second = symphony_logic.get_condensed_logic("sym-cache", use_cache=True)

    assert first == second
    assert fetch_calls["n"] == 1, "second cached call should not re-fetch"


def test_get_condensed_logic_use_cache_false_always_refetches(monkeypatch):
    """use_cache=False bypasses the cache and re-fetches every call."""
    import symphony_logic

    fixture_body = _load_fixture("sample_score_small.json")
    fetch_calls = {"n": 0}

    def _counting_fetch(sid):
        fetch_calls["n"] += 1
        return fixture_body

    monkeypatch.setattr(symphony_logic, "fetch_symphony_score", _counting_fetch)
    if hasattr(symphony_logic, "clear_cache"):
        symphony_logic.clear_cache()

    symphony_logic.get_condensed_logic("sym-nocache", use_cache=False)
    symphony_logic.get_condensed_logic("sym-nocache", use_cache=False)

    assert fetch_calls["n"] == 2, "use_cache=False must re-fetch every call"


def test_get_condensed_logic_caches_per_symphony_id(monkeypatch):
    """The cache is keyed by symphony id — distinct ids don't collide."""
    import symphony_logic

    bodies = {
        "small": _load_fixture("sample_score_small.json"),
        "large": _load_fixture("sample_score_large.json"),
    }

    def _fetch(sid):
        return bodies["large"] if sid == "id-large" else bodies["small"]

    monkeypatch.setattr(symphony_logic, "fetch_symphony_score", _fetch)
    if hasattr(symphony_logic, "clear_cache"):
        symphony_logic.clear_cache()

    small_summary = symphony_logic.get_condensed_logic("id-small", use_cache=True)
    large_summary = symphony_logic.get_condensed_logic("id-large", use_cache=True)

    # Different ids -> different cached entries, no cross-contamination.
    assert small_summary["name"] == bodies["small"]["name"]
    assert large_summary["name"] == bodies["large"]["name"]
    assert small_summary["name"] != large_summary["name"]


def test_clear_cache_forces_refetch(monkeypatch):
    """clear_cache() invalidates cached entries — the design's invalidation hook.

    Cache invalidation design (binding proposal for GREEN): an in-process
    dict keyed by symphony_id, with an explicit clear_cache() escape hatch.
    Symphony logic changes rarely (creator edits), the daemon is long-lived,
    and one /score call per analysis cycle is well within the 1 req/sec
    budget — so a process-lifetime cache with explicit invalidation is
    sufficient. No TTL needed; if GREEN wants a TTL it must still expose
    clear_cache() for deterministic testing.
    """
    import symphony_logic

    if not hasattr(symphony_logic, "clear_cache"):
        pytest.fail("symphony_logic must expose clear_cache() for invalidation")

    fixture_body = _load_fixture("sample_score_small.json")
    fetch_calls = {"n": 0}

    def _counting_fetch(sid):
        fetch_calls["n"] += 1
        return fixture_body

    monkeypatch.setattr(symphony_logic, "fetch_symphony_score", _counting_fetch)
    symphony_logic.clear_cache()

    symphony_logic.get_condensed_logic("sym-x", use_cache=True)
    symphony_logic.clear_cache()
    symphony_logic.get_condensed_logic("sym-x", use_cache=True)

    assert fetch_calls["n"] == 2, "clear_cache() should force a re-fetch"


# ===========================================================================
# Property-based invariants (hypothesis) — hold for ANY tree shape
# ===========================================================================

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# A recursive strategy that builds arbitrary symphony-tree-shaped dicts:
# nodes with a `step`, optional `ticker`, optional `name`, and `children`.
_tickers = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)
_steps = st.sampled_from(["root", "group", "if", "if-child", "asset", "filter", "wt-cash-equal"])


def _node_strategy():
    return st.recursive(
        st.builds(
            lambda step, ticker, name: {
                k: v
                for k, v in {
                    "step": step,
                    "ticker": ticker,
                    "name": name,
                }.items()
                if v is not None
            },
            _steps,
            st.one_of(st.none(), _tickers),
            st.one_of(st.none(), st.text(min_size=0, max_size=12)),
        ),
        lambda children: st.builds(
            lambda step, kids: {"step": step, "children": kids},
            _steps,
            st.lists(children, max_size=4),
        ),
        max_leaves=25,
    )


@settings(max_examples=75, deadline=None)
@given(tree=_node_strategy())
def test_property_summary_always_has_contract_keys(tree):
    """For ANY tree shape, the summary exposes the full contract key set."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(tree)
    assert _EXPECTED_SUMMARY_KEYS.issubset(summary.keys())


@settings(max_examples=75, deadline=None)
@given(tree=_node_strategy())
def test_property_collection_fields_are_never_none(tree):
    """asset_universe / indicators_used / group_labels are never None."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(tree)
    assert isinstance(summary["asset_universe"], list)
    assert isinstance(summary["indicators_used"], dict)
    assert isinstance(summary["group_labels"], list)


@settings(max_examples=75, deadline=None)
@given(tree=_node_strategy())
def test_property_tree_metrics_are_nonnegative_ints(tree):
    """tree_depth / tree_breadth / total_nodes are always non-negative ints."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(tree)
    for key in ("tree_depth", "tree_breadth", "total_nodes"):
        val = summary[key]
        assert isinstance(val, int) and not isinstance(val, bool), key
        assert val >= 0, key


@settings(max_examples=75, deadline=None)
@given(tree=_node_strategy())
def test_property_asset_universe_is_distinct(tree):
    """asset_universe never contains duplicate tickers, for any tree."""
    from symphony_logic import condense_symphony_logic

    universe = condense_symphony_logic(tree)["asset_universe"]
    assert len(universe) == len(set(universe))


@settings(max_examples=75, deadline=None)
@given(tree=_node_strategy())
def test_property_indicator_counts_are_positive(tree):
    """Every indicators_used count is a strictly positive int (a count of 0
    would mean the key should not be present at all)."""
    from symphony_logic import condense_symphony_logic

    indicators = condense_symphony_logic(tree)["indicators_used"]
    for count in indicators.values():
        assert isinstance(count, int) and not isinstance(count, bool)
        assert count > 0


@settings(max_examples=60, deadline=None)
@given(tree=_node_strategy())
def test_property_total_nodes_bounds_universe_and_groups(tree):
    """total_nodes is an upper bound on how many asset/group nodes exist, so
    it must be >= len(asset_universe) and >= len(group_labels). A summary
    that violates this has miscounted the walk."""
    from symphony_logic import condense_symphony_logic

    summary = condense_symphony_logic(tree)
    assert summary["total_nodes"] >= len(summary["asset_universe"])
    assert summary["total_nodes"] >= len(summary["group_labels"])
