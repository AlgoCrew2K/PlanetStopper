"""RED tests — Component 5b PRODUCTION-PATH wiring (AC-24 / AC-25 end-to-end).

WHY THIS FILE EXISTS (the C2-hollow trap, route-level-RED lesson):
  The C5b gate-engine unit tests (tests/advisors/test_cull_strengthening.py, 17/17)
  prove the GATE ENGINE bites WHEN FED the right inputs — a batch PBO and a
  spy_returns_fn. They are mutation-proven non-hollow. BUT the C5b diff touched ONLY
  advisors/backtest_gate_engine.py; the PRODUCTION entry point
  advisors/strategy_builder_engine.py::propose_strategies was never rewired, so the
  engine is never fed those inputs in the real pipeline:

    - propose_strategies builds each BacktestCandidate from
      ``result.daily_returns.values()`` (strategy_builder_engine.py:945) — the DATE
      KEYS are dropped, and BacktestCandidate is constructed WITHOUT ``dated_returns``
      (lines 951-959). With no dated_returns the gate cannot compute a batch PBO →
      ``_batch_pbo`` is None → the AC-24 PBO veto NEVER FIRES in production.

    - propose_strategies calls ``evaluate_candidate_batch(...)`` WITHOUT
      ``spy_returns_fn`` (lines 965-969), so ``_effective_default_oos_alpha`` stays at
      the always-0.0 ``default_oos_alpha`` → the AC-25 SPY-fold baseline NEVER BITES
      in production; the old beats-zero behaviour persists.

  So AC-24 ("a high-PBO candidate is VETOED") and AC-25 ("a positive-but-below-SPY
  candidate is REJECTED") are UNMET in the REAL pipeline, even though the engine
  unit-tests are green. These tests exercise the REAL ``propose_strategies`` public
  entry point end-to-end and MUST FAIL on the pre-wiring HEAD — that failure IS the
  empirical proof of the hollow gap (the same lesson as route-level RED: a test that
  mocks the whole gate passes while the live path is hollow).

WHAT IS MOCKED (and what is NOT):
  - MOCKED (network / IO only): ``run_backtest`` (returns DATE-KEYED daily_returns
    dicts we fully control — the date keys are the whole point), ``_has_composer_key``
    (→ True), ``_generate_candidate_trees`` (→ controlled CandidateInfo objects so the
    batch composition is deterministic), and ``database`` (no real persistence).
  - NEVER MOCKED: ``evaluate_candidate_batch`` (the real gate engine runs),
    ``math_engine.compute_pbo`` (the real PBO math runs on our date-keyed fixtures),
    the fold transform, and the BHY/Yekutieli FDR. The veto / baseline must fire
    THROUGH the real pipeline, not a stub.

SPY SEAM (AC-25): the plan specifies SPY is "sourced through the existing
  backtest/data path (no new endpoint)". The wiring impl (sb5b-wire) therefore sources
  the SPY series via ``run_backtest`` for the SPY symbol. Our mocked ``run_backtest``
  is SPY-AWARE: a call whose tree/symphony references SPY returns the controllable SPY
  series; all other calls return the per-candidate series. This pins the BEHAVIOUR
  (below-SPY → rejected with below_spy_alpha) without dictating the exact internal
  mechanism beyond the plan-mandated "existing backtest path".

NO HARDCODED PRODUCER VALUES: we assert the VERDICT / the rejection_reason token, and
  the RELATIONSHIP (below-SPY), never a literal pbo or alpha. The fixtures are fully
  controlled date->return dicts; the math runs for real on them.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from math_engine import PBO_REJECT_THRESHOLD, compute_pbo

# ---------------------------------------------------------------------------
# Module under test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbe():
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


# ---------------------------------------------------------------------------
# Date-keyed return fixtures (fully controlled — no producer values). These mirror
# the gate-engine file's fixtures so the REAL compute_pbo behaviour is identical.
# ---------------------------------------------------------------------------

# 80 trading-day-ish date strings across 8 months → 8 CSCV blocks of 10.
_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]


def _per_block_overfit_logreturns() -> list[dict[str, float]]:
    """K=8 configs, each spiking exactly ONE chronological block. Yields pbo=1.0 via
    the REAL compute_pbo (the IS-best on any combo is OOS-mediocre). Values are LOG
    returns (propose_strategies multiplies result.daily_returns by 100 → pct); the
    spike/level relationship survives the linear scaling."""
    blocks = [_DATES[i * 10 : (i + 1) * 10] for i in range(8)]
    configs: list[dict[str, float]] = []
    for b in range(8):
        cfg: dict[str, float] = {}
        for i, blk in enumerate(blocks):
            for d in blk:
                cfg[d] = 0.20 if i == b else -0.01
        configs.append(cfg)
    return configs


def _below_spy_logreturns(n: int, *, level: float) -> list[dict[str, float]]:
    """K=n DIVERSE flat-positive configs (distinct levels → low PBO via the real
    compute_pbo, so the PBO veto does NOT pre-empt the SPY baseline) all at/below the
    given level. Used for the AC-25 below-SPY production test."""
    return [{d: level * (0.5 + 0.1 * j) for d in _DATES} for j in range(n)]


def _spy_logreturns(level: float) -> dict[str, float]:
    """SPY date-keyed log returns, flat at `level` over the candidate span."""
    return {d: level for d in _DATES}


def _pbo_gamma() -> float:
    return 1.0


# ---------------------------------------------------------------------------
# Self-guards: prove the fixtures drive the REAL compute_pbo across / below the
# threshold, so the production tests are non-hollow (a real pbo>0.5 exists to veto on,
# and the below-SPY batch is genuinely LOW-pbo so it isolates the SPY cause).
# ---------------------------------------------------------------------------


def test_production_fixture_high_pbo_exceeds_threshold_via_real_compute_pbo():
    pbo = compute_pbo(_per_block_overfit_logreturns(), _DATES, _pbo_gamma())
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"high-PBO production fixture must exceed the reject threshold via real "
        f"compute_pbo; got {pbo}"
    )


def test_production_below_spy_fixture_is_low_pbo_via_real_compute_pbo():
    """The below-SPY batch must be LOW-pbo so the PBO veto does not mask the SPY
    cause (precedence: pbo_veto dominates below_spy_alpha)."""
    pbo = compute_pbo(_below_spy_logreturns(3, level=0.001), _DATES, _pbo_gamma())
    assert pbo <= PBO_REJECT_THRESHOLD, (
        f"below-SPY production fixture must be LOW-pbo so the SPY cause is isolated; got {pbo}"
    )


# ---------------------------------------------------------------------------
# Helpers to drive the REAL propose_strategies with controlled per-candidate series.
# ---------------------------------------------------------------------------


def _make_candidate_infos(sbe, series_by_id: dict[str, dict[str, float]]):
    """Build CandidateInfo objects whose tree carries a marker so the mocked
    run_backtest side_effect can map tree → the right date-keyed series."""
    infos = []
    for cid in series_by_id:
        # Minimal valid-enough tree; the marker is what the side_effect keys on. The
        # tree is never validated/compiled in this path (generation is mocked).
        tree = {"step": "root", "name": cid, "_series_id": cid, "children": []}
        infos.append(
            sbe.CandidateInfo(
                candidate_id=cid,
                tree=tree,
                template_id="T1",
                params={},
            )
        )
    return infos


def _collect_asset_tickers(tree: object) -> list[str]:
    """Deep-walk a Composer tree and collect every asset ticker (`{"step":"asset",
    "ticker": "..."}` nodes). Used to identify the SPY BENCHMARK tree by content."""
    tickers: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("step") == "asset" and isinstance(node.get("ticker"), str):
                tickers.append(node["ticker"].upper())
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return tickers


def _is_spy_benchmark_tree(tree: object) -> bool:
    """Identify the SPY BENCHMARK call by tree CONTENT (PM directive — NOT call-order,
    NOT 'any node mentions SPY'): the benchmark tree is the one whose root description
    marks it as the SPY benchmark, OR whose ONLY asset ticker is SPY. A candidate that
    merely HOLDS SPY among other constituents is therefore NOT mistaken for the
    benchmark (it has other tickers too). Matches the impl's
    make_root('SPY Benchmark', 'daily', [make_weight_equal([make_asset('SPY')])])."""
    if not isinstance(tree, dict):
        return False
    desc = str(tree.get("description", "")) + " " + str(tree.get("name", ""))
    if "SPY BENCHMARK" in desc.upper():
        return True
    tickers = _collect_asset_tickers(tree)
    return tickers == ["SPY"]


def _spy_aware_backtest(series_by_id: dict[str, dict[str, float]], spy_series: dict[str, float]):
    """Return a run_backtest side_effect that is SPY-aware: a call sourcing SPY (the
    wiring impl backtests a SPY benchmark tree through this SAME client per AC-25
    'existing backtest path') returns the controllable SPY series; a candidate call
    returns the per-candidate series keyed by the tree's _series_id marker.

    SEAM CONTRACT (content-based, PM-directed — never call-order):
      - The SPY benchmark call is identified by tree CONTENT — root description
        'SPY Benchmark' OR a tree whose ONLY asset ticker is SPY (see
        _is_spy_benchmark_tree). A candidate that itself holds SPY among other
        constituents is NOT the benchmark.
      - A candidate call is identified by its _series_id marker (our generator is
        mocked to stamp it; the impl just backtests whatever trees generation hands it).
      - A SPY symphony_id is also accepted as a fallback identifier.
    This pins behaviour (below-SPY → below_spy_alpha) without dictating mechanism
    beyond the plan's 'SPY via the existing backtest path'."""
    from advisors.composer_backtest_client import BacktestResult

    def _side_effect(tree, *, symphony_id="", **kwargs):
        # Candidate identity wins first (our marker) so a benchmark-shaped guess can
        # never override an explicit candidate.
        sid = tree.get("_series_id") if isinstance(tree, dict) else None
        is_candidate = sid in series_by_id
        if not is_candidate and ("SPY" in str(symphony_id).upper() or _is_spy_benchmark_tree(tree)):
            return BacktestResult(stats={"sharpe": 1.0}, data_warnings=[], daily_returns=dict(spy_series))
        # Candidate path: map the tree marker → its series (empty dict if unknown).
        series = series_by_id.get(sid, {})
        return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=dict(series))

    return _side_effect


def _all_rejection_reasons(result) -> list:
    """Collect rejection_reason from every gate result in the ProposalRun."""
    reasons = []
    for r in result.gated_batch.results:
        reasons.append(getattr(r, "rejection_reason", "<<no rejection_reason field>>"))
    return reasons


# ===========================================================================
# AC-24 PRODUCTION PATH — PBO veto must FIRE through the REAL propose_strategies.
# ===========================================================================


def test_ac24_production_high_pbo_candidate_is_pbo_vetoed_end_to_end(sbe):
    """END-TO-END (the C2-hollow proof): a high-PBO batch flowing through the REAL
    propose_strategies must produce at least one candidate whose rejection_reason
    contains 'pbo'. This proves production (a) preserves DATE-KEYED returns into
    BacktestCandidate.dated_returns and (b) the real compute_pbo → gate PBO veto
    fires through the live pipeline. MUST FAIL pre-wiring: today line 945 drops the
    date keys → _batch_pbo is None → no candidate ever carries a pbo reason."""
    configs = _per_block_overfit_logreturns()
    series_by_id = {f"hpbo-{i}": c for i, c in enumerate(configs)}
    infos = _make_candidate_infos(sbe, series_by_id)
    spy = _spy_logreturns(0.0)  # neutral SPY — PBO is the cause under test, not SPY

    with (
        patch.object(sbe, "_has_composer_key", return_value=True),
        patch.object(sbe, "_generate_candidate_trees", return_value=infos),
        patch.object(sbe, "run_backtest", side_effect=_spy_aware_backtest(series_by_id, spy)),
        patch.object(sbe, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=["AAA", "BBB"],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="prod-pbo",
        )

    assert result.error is None, f"propose_strategies errored: {result.error}"
    reasons = _all_rejection_reasons(result)
    assert any("pbo" in str(r).lower() for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-24): a high-PBO batch through the REAL "
        "propose_strategies produced NO candidate with a 'pbo' rejection_reason — "
        "the PBO veto never fired in production. Expected the date-keyed returns to be "
        f"threaded into BacktestCandidate.dated_returns and the veto to fire. Got reasons: {reasons}"
    )


# ===========================================================================
# AC-25 PRODUCTION PATH — SPY-fold baseline must BITE through the REAL propose_strategies.
# ===========================================================================


def test_ac25_production_below_spy_candidate_rejected_below_spy_alpha_end_to_end(sbe):
    """END-TO-END (the C2-hollow proof): a positive-but-below-SPY batch flowing
    through the REAL propose_strategies must produce at least one candidate whose
    rejection_reason is 'below_spy_alpha'. This proves production passes a
    spy_returns_fn (SPY sourced through the existing backtest path) so the SPY-fold
    baseline replaces the always-0.0 default and BITES. MUST FAIL pre-wiring: today
    no spy_returns_fn is passed → _effective_default_oos_alpha stays 0.0 → the
    below-SPY candidates clear on beats-zero and never carry below_spy_alpha."""
    # Candidates: diverse low-level positive returns (low PBO so SPY is the cause).
    configs = _below_spy_logreturns(3, level=0.0005)
    series_by_id = {f"below-{i}": c for i, c in enumerate(configs)}
    infos = _make_candidate_infos(sbe, series_by_id)
    # SPY clearly ABOVE every candidate's level → each candidate is below SPY over the fold.
    spy = _spy_logreturns(0.02)

    with (
        patch.object(sbe, "_has_composer_key", return_value=True),
        patch.object(sbe, "_generate_candidate_trees", return_value=infos),
        patch.object(sbe, "run_backtest", side_effect=_spy_aware_backtest(series_by_id, spy)),
        patch.object(sbe, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=["AAA", "BBB"],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="prod-spy",
        )

    assert result.error is None, f"propose_strategies errored: {result.error}"
    reasons = _all_rejection_reasons(result)
    assert any(str(r) == "below_spy_alpha" for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-25): a positive-but-below-SPY batch through the REAL "
        "propose_strategies produced NO candidate with a 'below_spy_alpha' "
        "rejection_reason — the SPY-fold baseline never bit in production (the "
        "always-0.0 beats-zero baseline persisted). Expected production to source SPY "
        f"and pass a spy_returns_fn into evaluate_candidate_batch. Got reasons: {reasons}"
    )
