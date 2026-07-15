"""RED tests — AC-3 Atlas corpus wiring inside advisors/frontrunner_builder.py.

Module under test: advisors.frontrunner_builder._gather_atlas_frontrunner_patterns
(the new function this batch introduces; NOT-STARTED as of bd165a5/ac8d313 —
frontrunner_builder.py's ``_build_generation_prompt`` already has the
``signal_context["atlas_patterns"]`` prompt slot (line ~498) but nothing ever
populates it — ``_run_build_for_symphony`` builds ``signal_context`` with only
``watched_tickers``).

CONTRACT SOURCE (feature-plans/frontrunner-builder.md AC-3):
  "Loads the Atlas frontrunner corpus through the shared 7-day cache (first
  real population; force_refresh optional), identifies frontrunner-shaped
  strategies (structural detection + name), extracts patterns (watched
  tickers, VIX/hedge instruments, RSI thresholds, basket shapes). Never
  trusts incoming oos_metrics.sharpe."

PATCH-TARGET STRATEGY (mirrors test_frontrunner_gate_wiring.py's own
documented convention): patch collaborators at their ORIGIN module —
advisors.community_strats.load_community_strategies (NOT a raw pymongo call;
community_strats.load_community_strategies already routes its own Atlas read
through atlas_cache.cached_pull internally — confirmed by reading the source,
community_strats.py:224-230 — so asserting THIS call site is the correct
AC-3 "through the shared 7-day cache" proof point, not a re-derivation of the
caching itself) and advisors.frontrunner_detector.detect_frontrunner_cascades
(the real structural-detection reuse AC-3 calls for — "structural detection",
not a bespoke re-implementation).

FIXTURE PROVENANCE: candidate trees are built via the REAL symphony_schema
constructors (same RSI-gt-80 -> VIXY-fire shape already proven detectable by
test_frontrunner_detector.py / test_frontrunner_builder.py's own
incumbent_symphony fixture), not hand-typed JSON — schema-derived, not
producer-captured (no live Atlas call is made anywhere in this file).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


def _make_frontrunner_shaped_tree(name: str = "Atlas Frontrunner Candidate") -> dict:
    """A minimal but genuinely-detectable frontrunner cascade tree — identical
    construction pattern to test_frontrunner_gate_wiring.py's incumbent_symphony
    fixture (RSI(SPY) gt 80 -> VIXY fire branch), reused here so the detector
    reliably finds it without re-deriving its own size-cliff/VIX-ticker
    signature requirements."""
    from advisors import symphony_schema

    cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "QQQ", window=10),
            "gt",
            81,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("UVXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root(name, "daily", [cascade])


def _make_non_frontrunner_tree(name: str = "Plain Buy-and-Hold") -> dict:
    """A flat asset-allocation tree with no if-node anywhere — structurally
    NOT frontrunner-shaped; frontrunner_detector.detect_frontrunner_cascades
    must report zero cascades for this (AC-2's own "no incumbent frontrunner"
    contract, reused here as the negative case for AC-3's "identifies
    frontrunner-shaped strategies" filter)."""
    from advisors import symphony_schema

    return symphony_schema.make_root(
        name,
        "daily",
        [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
    )


def _atlas_result(*candidates: dict, available: bool = True, reason: str | None = None) -> dict:
    """Build a community_strats.load_community_strategies-shaped return dict.
    Each positional candidate is a bare tree dict; wrapped into the real
    candidate-doc shape (sid/name/tree/tickers/oos_metrics/composition_hash)
    per community_strats.py:324-335's own documented output contract."""
    docs = []
    for i, tree in enumerate(candidates):
        docs.append(
            {
                "sid": f"atlas-candidate-{i}",
                "name": tree.get("name", f"candidate-{i}"),
                "tree": tree,
                "tickers": [],
                # Deliberately implausible/inflated sharpe on EVERY doc — AC-3's
                # "never trusts incoming oos_metrics.sharpe" must hold even when
                # the incoming data looks maximally attractive.
                "oos_metrics": {"sharpe": 99.0},
                "composition_hash": f"hash-{i}",
            }
        )
    return {
        "available": available,
        "candidates": docs,
        "stats": {},
        "source": "captplanet",
        **({"reason": reason} if reason else {}),
    }


# ---------------------------------------------------------------------------
# AC-3: routed through the shared 7-day cache (community_strats.load_community_strategies).
# ---------------------------------------------------------------------------


def test_gather_atlas_frontrunner_patterns_goes_through_the_shared_cache_loader(fbld):
    """AC-3 'through the shared 7-day cache' — the ONLY Atlas entry point this
    function may call is community_strats.load_community_strategies (which
    itself routes through atlas_cache.cached_pull internally); no raw pymongo
    or bespoke fetch is acceptable here."""
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(_make_frontrunner_shaped_tree()),
    ) as mock_load:
        fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert mock_load.called, (
        "advisors.community_strats.load_community_strategies was never called — "
        "AC-3 requires the Atlas frontrunner corpus to be loaded through the "
        "shared weekly cache, not a fresh/bespoke fetch"
    )


# ---------------------------------------------------------------------------
# AC-3: structural detection + name — identifies frontrunner-shaped strategies.
# ---------------------------------------------------------------------------


def test_gather_atlas_frontrunner_patterns_extracts_patterns_from_detected_cascades(fbld):
    """A genuinely frontrunner-shaped Atlas candidate (real detectable RSI->VIX
    cascade, verified via the REAL frontrunner_detector — never mocked) must
    contribute at least one pattern dict carrying its VIX ticker(s) and RSI
    threshold(s) — the concrete signal AC-3 says Fable needs for grounding."""
    tree = _make_frontrunner_shaped_tree()

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(tree),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert isinstance(patterns, list) and patterns, (
        "expected at least one extracted pattern from a genuinely "
        "frontrunner-shaped Atlas candidate — got an empty list"
    )
    # At least one pattern must carry the UVXY ticker this fixture's fire
    # branch actually contains (derived from the fixture, not hardcoded to a
    # specific dict shape the implementer hasn't chosen yet).
    found_vix_ticker = any(
        "UVXY" in (p.get("vix_tickers") or []) for p in patterns if isinstance(p, dict)
    )
    assert found_vix_ticker, (
        f"no extracted pattern carries the fixture's own VIX-family ticker "
        f"(UVXY) in a 'vix_tickers' field — patterns={patterns!r}"
    )
    found_threshold = any(
        any(abs(t - 81) < 1e-6 for t in (p.get("rsi_thresholds") or []))
        for p in patterns
        if isinstance(p, dict)
    )
    assert found_threshold, (
        f"no extracted pattern carries the fixture's own RSI threshold (81) "
        f"in an 'rsi_thresholds' field — patterns={patterns!r}"
    )


def test_gather_atlas_frontrunner_patterns_skips_non_frontrunner_shaped_candidates(fbld):
    """A flat buy-and-hold Atlas candidate (no if-node anywhere, real
    detect_frontrunner_cascades result is zero cascades) must NOT contribute
    any pattern — 'identifies frontrunner-shaped strategies' means filtering
    OUT the ones that aren't, not extracting noise from every doc regardless
    of shape."""
    plain_tree = _make_non_frontrunner_tree()

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(plain_tree),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["SPY"])

    assert patterns == [], (
        f"a structurally non-frontrunner Atlas candidate produced pattern(s) "
        f"anyway — AC-3's structural-detection filter is not excluding "
        f"non-matching docs: {patterns!r}"
    )


def test_gather_atlas_frontrunner_patterns_mixed_corpus_extracts_only_the_shaped_one(fbld):
    """A corpus containing BOTH a frontrunner-shaped and a plain candidate
    must extract pattern(s) only from the shaped one — proves the filter is
    applied per-candidate, not corpus-wide."""
    shaped = _make_frontrunner_shaped_tree("Shaped")
    plain = _make_non_frontrunner_tree("Plain")

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(shaped, plain),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert patterns, "expected at least one pattern from the shaped candidate in a mixed corpus"
    assert any("UVXY" in (p.get("vix_tickers") or []) for p in patterns if isinstance(p, dict))


# ---------------------------------------------------------------------------
# AC-3: never trusts incoming oos_metrics.sharpe.
# ---------------------------------------------------------------------------


def test_gather_atlas_frontrunner_patterns_never_carries_oos_metrics_or_sharpe(fbld):
    """AC-3: 'Never trusts incoming oos_metrics.sharpe.' Every extracted
    pattern must be built PURELY from structural detection (VIX tickers, RSI
    thresholds, watched tickers) — never echoing the Atlas doc's own
    self-reported oos_metrics/sharpe into the pattern, which would let an
    Atlas doc's UNVERIFIED performance claim leak into Fable's prompt as if
    it were trusted signal."""
    tree = _make_frontrunner_shaped_tree()

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(tree),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert patterns, "fixture must produce at least one pattern for this assertion to be meaningful"
    for p in patterns:
        assert isinstance(p, dict)
        assert "oos_metrics" not in p, f"pattern leaks oos_metrics: {p!r}"
        assert "sharpe" not in p, f"pattern leaks a bare sharpe field: {p!r}"


# ---------------------------------------------------------------------------
# D-1: never raises; honest degradation when Atlas is unavailable/malformed.
# ---------------------------------------------------------------------------


def test_gather_atlas_frontrunner_patterns_returns_empty_list_when_atlas_unavailable(fbld):
    """Atlas down / stale-cache-exhausted (available=False) -> empty list,
    never a crash, never a fabricated pattern (AC-11: 'Atlas down -> stale-
    cache degrade -> skip')."""
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(available=False, reason="AtlasFetchTimeout"),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert patterns == []


def test_gather_atlas_frontrunner_patterns_never_raises_on_malformed_candidate_docs(fbld):
    """D-1: a malformed candidate doc (missing 'tree', tree=None, tree not a
    dict) must degrade that ONE doc silently, never crash the whole call —
    mirrors frontrunner_detector's own 'never raises' contract on malformed
    input, since this function feeds candidate trees straight into it."""
    malformed_result = {
        "available": True,
        "candidates": [
            {"sid": "no-tree-key", "name": "x"},
            {"sid": "none-tree", "name": "y", "tree": None},
            {"sid": "string-tree", "name": "z", "tree": "not-a-dict"},
        ],
        "stats": {},
        "source": "captplanet",
    }

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=malformed_result,
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(
            watched_tickers=["QQQ"]
        )  # must not raise

    assert patterns == []


def test_gather_atlas_frontrunner_patterns_never_raises_when_loader_itself_raises(fbld):
    """D-1: even if community_strats.load_community_strategies somehow raises
    (violating its own documented never-raises contract), this function must
    still degrade to an empty list rather than propagate — defense in depth,
    matching every other producer call site's D-1 posture in this module."""
    with patch(
        "advisors.community_strats.load_community_strategies",
        side_effect=RuntimeError("unexpected"),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(
            watched_tickers=["QQQ"]
        )  # must not raise

    assert patterns == []


# ---------------------------------------------------------------------------
# Integration: _run_build_for_symphony actually populates signal_context.
# ---------------------------------------------------------------------------


def test_run_build_for_symphony_populates_atlas_patterns_in_the_signal_context(fbld):
    """End-to-end wiring check: signal_context["atlas_patterns"] (the prompt
    slot already read by _build_generation_prompt, frontrunner_builder.py:498)
    must actually be populated from a real Atlas frontrunner-shaped candidate
    during a real build run — the prompt slot existing is not enough; AC-3 is
    about it being FED, which is the gap this whole batch closes."""
    from advisors import symphony_schema

    incumbent_cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    incumbent_symphony = symphony_schema.make_root(
        "Incumbent Test Symphony", "daily", [incumbent_cascade]
    )

    atlas_tree = _make_frontrunner_shaped_tree()
    captured_contexts: list[dict] = []

    def _capture_generate(signal_context, **kwargs):
        captured_contexts.append(signal_context)
        return fbld.GenerationResult(candidate=None, error="stop-after-capture")

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.community_strats.load_community_strategies",
            return_value=_atlas_result(atlas_tree),
        ),
        patch.object(fbld, "generate_candidate_overlay", side_effect=_capture_generate),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert captured_contexts, "generate_candidate_overlay was never reached"
    signal_context = captured_contexts[0]
    atlas_patterns = signal_context.get("atlas_patterns")
    assert atlas_patterns, (
        f"signal_context['atlas_patterns'] is empty/missing during a real "
        f"build run with an available, frontrunner-shaped Atlas candidate — "
        f"the prompt slot exists but is never fed: signal_context={signal_context!r}"
    )
