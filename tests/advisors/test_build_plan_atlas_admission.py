"""RED tests — Component 2b: Atlas objective-matched suggestion (advisors/build_plan_generator.py).

Module under test: advisors.build_plan_generator (NEW).

SCOPE: Component 2b ONLY (AC-12 objective-matched Atlas admission; AC-13 provenance
tagging — the C2/2b slice only). Component 1 is DONE; Component 3/4/5 are later phases.

AC-13 PHASE BOUNDARY (PM refinement D): at C2/2b we RED-test ONLY
  (1) generator output provenance='built-new' (covered in test_build_plan_generator.py),
  (2) Atlas admission provenance='atlas-suggested',
  (3) the POOLING function tags both sources correctly + preserves the tags.
DEFERRED to C3/C5 (forward-AC, not tested here): both sources through the SAME single-
batch FDR gate, gate count includes both, tag survives to persisted raw_response +
route/SPA JSON. Those require the compiler (C3) and the rewired route (C5).

CONTRACT for AC-12 objective-matched admission (PM-approved):
  - Source: load_community_strategies(force_refresh=False) -> {available, candidates:[...]}.
    Each candidate carries oos_metrics (a dict, possibly missing the needed stat).
  - Ranking per objective (by the named stat the loader returns):
      cut_drawdown         -> lowest drawdown FIRST (most-negative / smallest magnitude rule
                              pinned by the fixture below: 'max_drawdown' nearer zero = better)
      volatility_mitigation-> lowest volatility FIRST ('volatility')
      lift_risk_adjusted   -> best risk-adjusted FIRST ('sharpe', higher = better)
      diversify            -> low cross-correlation vs the admitted set (handled deterministically)
  - Missing-stat docs: KEPT-LAST (admitted after all docs that HAVE the stat). Never crash.
  - Bounded by MAX_COMMUNITY_CANDIDATES_PER_RUN.
  - Each admitted candidate tagged provenance='atlas-suggested'.

ADVERSARIAL FOCUS: assert ORDER / PRESENCE / TAG / BOUND only. The oos_metric inputs
are crafted fixtures we fully control — there are no hardcoded PRODUCER-computed values
(we assert the admitted docs are returned in the objective-best ORDER of the stats WE
supplied, not any value the loader computed). The math engine is never mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import guard (RED until the module exists).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bpg():
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


def _objective(bpg, name: str):
    return bpg.Objective[name]


# ---------------------------------------------------------------------------
# Community-result fixture builders. We craft sid + oos_metrics we control;
# the admission ORDER must follow the stat values WE set (no producer values).
# A community candidate mirrors the load_community_strategies output shape:
#   {sid, name, tree, tickers, oos_metrics, composition_hash}
# ---------------------------------------------------------------------------


def _candidate(sid: str, oos_metrics: dict | None) -> dict:
    return {
        "sid": sid,
        "name": f"Community {sid}",
        # A minimal valid-shaped tree placeholder; admission ranks on oos_metrics,
        # not on the tree, so the tree content is irrelevant to AC-12 ordering.
        "tree": {"step": "root", "name": sid, "rebalance": "daily", "children": []},
        "tickers": {"SPY"},
        "oos_metrics": oos_metrics,
        "composition_hash": f"hash-{sid}",
    }


def _community_result(candidates: list[dict], *, available: bool = True) -> dict:
    return {
        "available": available,
        "candidates": candidates,
        "stats": {"pulled": len(candidates), "valid": len(candidates)},
        "source": "captplanet",
    }


def _sids(admitted: list) -> list[str]:
    """Extract the sid order from admitted CandidateInfo-like objects.

    Admitted candidates are CandidateInfo objects (candidate_id == sid) OR dicts
    carrying 'sid'/'candidate_id'. We resolve the sid robustly so the test does
    not over-constrain the container shape the implementer chooses.
    """
    out = []
    for c in admitted:
        sid = (
            getattr(c, "candidate_id", None)
            or (c.get("sid") if isinstance(c, dict) else None)
            or (c.get("candidate_id") if isinstance(c, dict) else None)
        )
        out.append(sid)
    return out


def _provenance(c) -> str | None:
    """Resolve the provenance tag from an admitted candidate (object or dict)."""
    if isinstance(c, dict):
        return c.get("provenance") or (c.get("params", {}) or {}).get("provenance")
    # CandidateInfo: provenance carried on .params or a direct .provenance attr.
    prov = getattr(c, "provenance", None)
    if prov:
        return prov
    params = getattr(c, "params", None)
    if isinstance(params, dict):
        return params.get("provenance")
    return None


# ===========================================================================
# SECTION 1 — AC-12: objective-matched admission ranking by the named stat
# ===========================================================================


def test_ac12_cut_drawdown_admits_lowest_drawdown_first(bpg):
    """AC-12 cut_drawdown: docs ranked by drawdown, smallest-magnitude drawdown FIRST.

    We supply max_drawdown values (quantstats convention: <= 0; nearer zero = shallower
    = better defensive). The admitted ORDER must reflect OUR values, best-first."""
    cands = [
        _candidate("deep", {"max_drawdown": -0.55}),  # worst (deepest)
        _candidate("shallow", {"max_drawdown": -0.10}),  # best (shallowest)
        _candidate("mid", {"max_drawdown": -0.30}),
    ]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "cut_drawdown")
    )
    assert _sids(admitted) == ["shallow", "mid", "deep"]


def test_ac12_volatility_mitigation_admits_lowest_volatility_first(bpg):
    """AC-12 volatility_mitigation: docs ranked by volatility, lowest FIRST."""
    cands = [
        _candidate("hi", {"volatility": 0.40}),
        _candidate("lo", {"volatility": 0.08}),
        _candidate("mid", {"volatility": 0.22}),
    ]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "volatility_mitigation")
    )
    assert _sids(admitted) == ["lo", "mid", "hi"]


def test_ac12_lift_risk_adjusted_admits_best_sharpe_first(bpg):
    """AC-12 lift_risk_adjusted: docs ranked by sharpe, highest FIRST."""
    cands = [
        _candidate("low", {"sharpe": 0.2}),
        _candidate("high", {"sharpe": 2.1}),
        _candidate("mid", {"sharpe": 1.0}),
    ]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "lift_risk_adjusted")
    )
    assert _sids(admitted) == ["high", "mid", "low"]


def test_ac12_diversify_admission_is_deterministic_and_complete(bpg):
    """AC-12 diversify: ranked by low cross-correlation vs the admitted set. We assert
    the admission is DETERMINISTIC (same input -> same order) and admits all docs
    (bounded by the cap) — never crashes. Cross-correlation ordering is an internal
    detail; we pin determinism + completeness, not a producer-computed corr value."""
    cands = [_candidate(f"d{i}", {"sharpe": 1.0, "volatility": 0.2}) for i in range(5)]
    admitted_a = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "diversify")
    )
    admitted_b = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "diversify")
    )
    assert _sids(admitted_a) == _sids(admitted_b), "diversify admission must be deterministic"
    assert set(_sids(admitted_a)) == {f"d{i}" for i in range(5)}


def test_ac12_missing_stat_doc_kept_last(bpg):
    """AC-12 (refinement / PM answer): a doc lacking the objective stat is KEPT-LAST,
    admitted after all docs that HAVE the stat. Never excluded, never crashes."""
    cands = [
        _candidate("hasA", {"max_drawdown": -0.20}),
        _candidate("nostat", {}),  # no max_drawdown
        _candidate("hasB", {"max_drawdown": -0.05}),
        _candidate("nostat2", None),  # oos_metrics is None entirely
    ]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "cut_drawdown")
    )
    order = _sids(admitted)
    # The two stat-bearing docs come first, best-first; missing-stat docs trail.
    assert order[:2] == ["hasB", "hasA"]
    assert set(order[2:]) == {"nostat", "nostat2"}
    assert len(order) == 4  # nothing dropped


def test_ac12_admission_bounded_by_max_community_constant(bpg):
    """AC-12: the admitted count is bounded by MAX_COMMUNITY_CANDIDATES_PER_RUN."""
    cap = bpg.MAX_COMMUNITY_CANDIDATES_PER_RUN
    cands = [_candidate(f"c{i}", {"sharpe": float(i)}) for i in range(cap + 7)]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "lift_risk_adjusted")
    )
    assert len(admitted) == cap
    # And the cap keeps the BEST (highest-sharpe) docs, not an arbitrary slice.
    best_sids = [f"c{i}" for i in range(cap + 6, cap + 6 - cap, -1)]
    assert _sids(admitted) == best_sids


def test_ac12_explicit_max_candidates_override_respected(bpg):
    """AC-12: an explicit max_candidates kwarg further bounds the admitted count."""
    cands = [_candidate(f"c{i}", {"sharpe": float(i)}) for i in range(10)]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "lift_risk_adjusted"), max_candidates=3
    )
    assert len(admitted) == 3
    assert _sids(admitted) == ["c9", "c8", "c7"]


# ===========================================================================
# SECTION 2 — AC-12: weekly-cache / bill-protection + honest degradation
# ===========================================================================


def test_ac12_uses_force_refresh_false_when_loading_from_atlas(bpg):
    """AC-12: when the module loads community strategies itself, it MUST pass
    force_refresh=False (weekly cache, operator bill-protection directive)."""
    captured = {}

    def _fake_loader(*args, **kwargs):
        captured["force_refresh"] = kwargs.get("force_refresh", "MISSING")
        return _community_result([_candidate("x", {"sharpe": 1.0})])

    # The module exposes a thin loader entry point that wraps load_community_strategies.
    with patch("advisors.community_strats.load_community_strategies", side_effect=_fake_loader):
        bpg.load_atlas_candidates(_objective(bpg, "lift_risk_adjusted"))
    assert captured.get("force_refresh") is False


def test_ac12_unavailable_atlas_degrades_to_empty_admission(bpg):
    """AC-12 honest degradation: an unavailable Atlas result yields an empty admission,
    never a crash."""
    admitted = bpg.admit_community_candidates(
        _community_result([], available=False), _objective(bpg, "cut_drawdown")
    )
    assert admitted == []


def test_ac12_empty_candidates_degrades_to_empty_admission(bpg):
    """AC-12: an available-but-empty candidate list yields an empty admission."""
    admitted = bpg.admit_community_candidates(_community_result([]), _objective(bpg, "diversify"))
    assert admitted == []


def test_ac12_malformed_community_result_never_raises(bpg):
    """AC-12 D-1: a malformed community result (None / wrong type) degrades to empty,
    never raises."""
    assert bpg.admit_community_candidates(None, _objective(bpg, "diversify")) == []
    assert bpg.admit_community_candidates({}, _objective(bpg, "diversify")) == []
    assert (
        bpg.admit_community_candidates(
            {"available": True, "candidates": "not-a-list"}, _objective(bpg, "diversify")
        )
        == []
    )


def test_ac12_unparseable_stat_value_treated_as_missing_kept_last(bpg):
    """AC-12 robustness: a non-numeric stat value is treated as missing -> kept-last,
    never crashes the ranking."""
    cands = [
        _candidate("good", {"sharpe": 1.5}),
        _candidate("bad", {"sharpe": "not-a-number"}),
    ]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "lift_risk_adjusted")
    )
    order = _sids(admitted)
    assert order[0] == "good"
    assert order[-1] == "bad"
    assert len(order) == 2


# ===========================================================================
# SECTION 3 — AC-13: provenance tagging (atlas-suggested) + pooling
# ===========================================================================


def test_ac13_admitted_community_candidates_tagged_atlas_suggested(bpg):
    """AC-13 (C2/2b slice): every admitted community candidate carries
    provenance='atlas-suggested'."""
    cands = [_candidate("a", {"sharpe": 1.0}), _candidate("b", {"sharpe": 2.0})]
    admitted = bpg.admit_community_candidates(
        _community_result(cands), _objective(bpg, "lift_risk_adjusted")
    )
    assert admitted
    for c in admitted:
        assert _provenance(c) == bpg.PROVENANCE_ATLAS_SUGGESTED


def test_ac13_pool_candidates_preserves_both_provenance_tags(bpg):
    """AC-13 (C2/2b slice, refinement D-3): the pooling function combines built-new and
    atlas-suggested sources into ONE list, preserving each item's provenance tag."""
    built_new = [
        {"plan_id": "bn1", "provenance": bpg.PROVENANCE_BUILT_NEW},
        {"plan_id": "bn2", "provenance": bpg.PROVENANCE_BUILT_NEW},
    ]
    atlas = bpg.admit_community_candidates(
        _community_result([_candidate("a", {"sharpe": 1.0})]),
        _objective(bpg, "lift_risk_adjusted"),
    )
    pooled = bpg.pool_candidates(built_new, atlas)
    # The pool contains BOTH sources.
    assert len(pooled) == len(built_new) + len(atlas)
    provenances = {_provenance(c) for c in pooled}
    assert provenances == {bpg.PROVENANCE_BUILT_NEW, bpg.PROVENANCE_ATLAS_SUGGESTED}


def test_ac13_pool_candidates_count_includes_both_sources(bpg):
    """AC-13 (C2/2b slice): the pooled batch count == built-new count + atlas count
    (no source silently dropped). The 'same FDR gate / count includes both' end-to-end
    assertion is DEFERRED to C3/C5 where the compiler + route make it runnable."""
    built_new = [{"plan_id": f"bn{i}", "provenance": bpg.PROVENANCE_BUILT_NEW} for i in range(4)]
    atlas = bpg.admit_community_candidates(
        _community_result([_candidate(f"a{i}", {"sharpe": float(i)}) for i in range(3)]),
        _objective(bpg, "lift_risk_adjusted"),
    )
    pooled = bpg.pool_candidates(built_new, atlas)
    assert len(pooled) == 4 + len(atlas)
    n_built = sum(1 for c in pooled if _provenance(c) == bpg.PROVENANCE_BUILT_NEW)
    n_atlas = sum(1 for c in pooled if _provenance(c) == bpg.PROVENANCE_ATLAS_SUGGESTED)
    assert n_built == 4
    assert n_atlas == len(atlas)


def test_ac13_pool_candidates_empty_sources_yield_empty_pool(bpg):
    """AC-13: pooling two empty sources yields an empty pool — never raises."""
    assert bpg.pool_candidates([], []) == []


def test_ac13_pool_candidates_one_empty_source_preserves_the_other(bpg):
    """AC-13: pooling when one source is empty preserves the other source intact."""
    built_new = [{"plan_id": "bn1", "provenance": bpg.PROVENANCE_BUILT_NEW}]
    pooled_a = bpg.pool_candidates(built_new, [])
    assert len(pooled_a) == 1
    assert _provenance(pooled_a[0]) == bpg.PROVENANCE_BUILT_NEW

    atlas = bpg.admit_community_candidates(
        _community_result([_candidate("a", {"sharpe": 1.0})]),
        _objective(bpg, "lift_risk_adjusted"),
    )
    pooled_b = bpg.pool_candidates([], atlas)
    assert len(pooled_b) == len(atlas)
    assert all(_provenance(c) == bpg.PROVENANCE_ATLAS_SUGGESTED for c in pooled_b)
