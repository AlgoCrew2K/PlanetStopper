"""RED tests — AC-4/AC-5 PRODUCTION-PATH wiring for asset_swap_engine.py.

R2-3 RECONCILIATION (2026-07-14): the deterministic
generate_objective_directed_candidates seam this file used to mock directly
was DELETED (R2-3, [PM-ASSUMED Q4]) and replaced by the LLM-reasoned
generate_reasoned_swap_candidates, which returns full (incumbent, candidate)
SwapCandidate PAIRS rather than a ranked ticker-only candidate list. Every
mock in this file that patched the old generator now patches the new one,
constructing SwapCandidate pairs with the fixed incumbent "AAA" (the sole
holding in _raw_tree()) — the PBO/SPY production-wiring math this file
proves is unaffected by that swap (candidate generation is upstream of the
gate this file actually tests); only the mocked SEAM name and return shape
changed.

WHY THIS FILE EXISTS (mirrors the C2-hollow-trap lesson from
tests/advisors/test_cull_production_wiring.py, the Strategy Builder C5b
production-wiring proof — same trap, different engine):

  audit F2 (ADVISOR-INTENT-AUDIT.md) traced asset_swap_engine.py's THREE
  evaluate_candidate_batch call sites (:1047 defensive zero-candidate branch,
  :1075 the N=1 operator-Evaluate path inside propose_operator_swap, :1277 the
  real batch path inside suggest_swaps) and found none pass dated_returns= (via
  the single BacktestCandidate(...) construction in _evaluate_single_variant)
  or spy_returns_fn=. Consequences:
    - _batch_pbo stays None forever -> the PBO overfitting veto is structurally
      incapable of firing (AC-24-style gap, mirrors the pre-DE-SB-CULL-001 SB bug).
    - default_oos_alpha stays the always-0.0 "beats a flat return" baseline
      instead of the SPY-relative baseline sourced by Strategy Builder
      (sbe.py:807-826) — the exact permissiveness AC-25/edge-14 eliminated
      for SB only.

  These tests drive the REAL propose_operator_swap / suggest_swaps public
  entry points end-to-end and MUST FAIL on the pre-wiring HEAD — that failure
  IS the empirical proof of the hollow gap (route-level RED, not an engine-gate
  unit test that mocks past the hollow call site).

SCOPE PER PM DIRECTIVE (team-lead scope-precision message): AC-4/AC-5 wiring
  is required at ALL THREE call sites in this file, unconditionally — including
  the N=1 operator-Evaluate site. At N=1, PBO stays structurally inert (the
  _PBO_MIN_CONFIGS=2 guard in backtest_gate_engine.py, NOT missing wiring) but
  the SPY-fold baseline is a REAL, visible behavior change: a single candidate
  that beats a flat 0.0% but loses to SPY must flip from ADOPT/KEEP_INCUMBENT
  to WITHHOLD with rejection_reason == "below_spy_alpha".

WHAT IS MOCKED (and what is NOT), mirroring test_cull_production_wiring.py:
  - MOCKED (network / IO / candidate-ranking only): run_backtest (returns
    DATE-KEYED daily_returns dicts we fully control), _has_composer_key
    (-> True), generate_objective_directed_candidates (-> a controlled ticker
    list so the batch composition is deterministic), and database (no real
    persistence).
  - NEVER MOCKED: evaluate_candidate_batch (the real gate engine runs),
    math_engine.compute_pbo (the real PBO math runs on our date-keyed
    fixtures), the fold transform, BHY/Yekutieli FDR, apply_ticker_swap,
    extract_tickers.

NO HARDCODED PRODUCER VALUES: assertions target the VERDICT / rejection_reason
  token and the RELATIONSHIP (below-SPY, high-PBO), never a literal pbo or
  alpha value. Fixtures are fully controlled date->return dicts; the math runs
  for real on them — identical fixture-construction approach to
  test_cull_production_wiring.py so the real compute_pbo behaviour is provably
  identical across both files.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from math_engine import PBO_REJECT_THRESHOLD, compute_pbo

# ---------------------------------------------------------------------------
# Module under test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


# ---------------------------------------------------------------------------
# Date-keyed return fixtures — IDENTICAL construction approach to
# test_cull_production_wiring.py so the real compute_pbo behaviour is provably
# the same math, just exercised through a different engine's call sites.
# ---------------------------------------------------------------------------

_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]


def _per_block_overfit_logreturns() -> list[dict[str, float]]:
    """K=8 configs, each spiking exactly ONE chronological block -> pbo=1.0 via
    the REAL compute_pbo (values are pct-scale directly; the engine already
    multiplies raw log-returns by 100.0, so we author fixtures in the SAME pct
    scale the gate consumes, matching how run_backtest's daily_returns are
    treated at the call sites under test)."""
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
    """K=n DIVERSE flat-positive configs (distinct levels -> low PBO via the
    real compute_pbo) all at/below the given level -- isolates the SPY cause
    from the PBO veto (precedence: pbo_veto dominates below_spy_alpha)."""
    return [{d: level * (0.5 + 0.1 * j) for d in _DATES} for j in range(n)]


def _spy_logreturns(level: float) -> dict[str, float]:
    return {d: level for d in _DATES}


def _pbo_gamma() -> float:
    return 1.0


def test_production_fixture_high_pbo_exceeds_threshold_via_real_compute_pbo():
    pbo = compute_pbo(_per_block_overfit_logreturns(), _DATES, _pbo_gamma())
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"high-PBO production fixture must exceed the reject threshold; got {pbo}"
    )


def test_production_below_spy_fixture_is_low_pbo_via_real_compute_pbo():
    pbo = compute_pbo(_below_spy_logreturns(3, level=0.001), _DATES, _pbo_gamma())
    assert pbo <= PBO_REJECT_THRESHOLD, (
        f"below-SPY production fixture must be LOW-pbo so the SPY cause is isolated; got {pbo}"
    )


# ---------------------------------------------------------------------------
# Tree helpers. Tickers "AAA" (incumbent) / "CAND{i}" (candidates) / "SPY"
# never collide, so content-based dispatch in the mocked run_backtest is
# unambiguous.
# ---------------------------------------------------------------------------


def _raw_tree(incumbent_ticker: str = "AAA") -> dict:
    """A REAL, structurally-valid Composer tree holding one asset. R2-3:
    _evaluate_single_variant's new validate_tree guard (wired unconditionally
    for every swap variant) rejects the old hand-built {"ticker": None,
    "children": [...]} minimal dict this helper used pre-R2-3 — validate_tree
    requires the real Composer "step" vocabulary. Built via the real
    symphony_schema constructors (the same ones _spy_returns_fn_for already
    uses elsewhere), so it is genuinely valid, not another hand-guessed shape.
    extract_tickers/_only_ticker still find exactly one ticker (AAA) in this
    shape -- unaffected by the switch."""
    from advisors import symphony_schema  # noqa: PLC0415

    return symphony_schema.make_root(
        "Production Wiring Test Symphony",
        "daily",
        [symphony_schema.make_weight_equal([symphony_schema.make_asset(incumbent_ticker)])],
    )


def _only_ticker(tree: object) -> str | None:
    """Return the single ticker in `tree` if there is exactly one, else None
    (used to identify a pure-SPY benchmark tree by content, never by call
    order — mirrors test_cull_production_wiring.py's _is_spy_benchmark_tree)."""
    tickers: set[str] = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("ticker"):
                tickers.add(node["ticker"])
            for child in node.get("children", []) or []:
                stack.append(child)
    return next(iter(tickers)) if len(tickers) == 1 else None


def _spy_aware_backtest(
    series_by_ticker: dict[str, dict[str, float]],
    spy_series: dict[str, float],
    *,
    baseline_series: dict[str, float] | None = None,
):
    """run_backtest side_effect, content-dispatched (never call-order-dispatched):
    - a tree whose ONLY ticker is "SPY" -> the SPY series (the AC-5 "existing
      backtest path" seam — mirrors sbe.py's SPY-benchmark sourcing).
    - a tree containing a ticker in series_by_ticker -> that candidate's series.
    - anything else (the raw incumbent tree, used for both the
      _evaluate_single_variant baseline call and propose_operator_swap's own
      duplicate _backtest_returns_from_tree call, AC-13's target) -> a neutral
      baseline series that never drives a veto by itself.
    """
    from advisors.composer_backtest_client import BacktestResult

    _baseline = baseline_series or {d: -0.001 for d in _DATES}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        only = _only_ticker(tree)
        if only == "SPY":
            return BacktestResult(
                stats={"sharpe": 1.0}, data_warnings=[], daily_returns=dict(spy_series)
            )
        if only in series_by_ticker:
            return BacktestResult(
                stats={"sharpe": 0.5}, data_warnings=[], daily_returns=dict(series_by_ticker[only])
            )
        # Baseline / incumbent tree (ticker "AAA").
        return BacktestResult(
            stats={"sharpe": 0.4}, data_warnings=[], daily_returns=dict(_baseline)
        )

    return _side_effect


def _all_rejection_reasons(result) -> list:
    return [
        getattr(r, "rejection_reason", "<<no rejection_reason field>>")
        for r in result.gate_batch.results
    ]


# ===========================================================================
# AC-4 BATCH — PBO veto must FIRE through the REAL suggest_swaps.
# ===========================================================================


def test_ac4_production_high_pbo_batch_is_pbo_vetoed_end_to_end(ase):
    """MUST FAIL pre-wiring: today the sole BacktestCandidate(...) construction
    omits dated_returns= -> _batch_pbo stays None -> no candidate can ever carry
    a 'pbo' rejection_reason, no matter how overfit the batch is."""
    configs = _per_block_overfit_logreturns()
    series_by_ticker = {f"CAND{i}": c for i, c in enumerate(configs)}
    reasoned_pairs = [
        ase.SwapCandidate(incumbent_asset="AAA", candidate_asset=t, rationale="x")
        for t in series_by_ticker
    ]
    spy = _spy_logreturns(0.0)  # neutral SPY — PBO is the cause under test.

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "generate_reasoned_swap_candidates", return_value=reasoned_pairs),
        patch.object(ase, "run_backtest", side_effect=_spy_aware_backtest(series_by_ticker, spy)),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.suggest_swaps(
            symphony_id="prod-pbo-swap",
            score_tree=_raw_tree(),
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
            correlation_data={},
            available_assets=list(series_by_ticker),
        )

    reasons = _all_rejection_reasons(result)
    assert any("pbo" in str(r).lower() for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-4, asset_swap_engine): a high-PBO batch through "
        "the REAL suggest_swaps produced NO candidate with a 'pbo' rejection_reason "
        f"— the PBO veto never fired in production. Got reasons: {reasons}"
    )


# ===========================================================================
# AC-5 BATCH — SPY-fold baseline must BITE through the REAL suggest_swaps.
# ===========================================================================


def test_ac5_production_below_spy_batch_rejected_below_spy_alpha_end_to_end(ase):
    """MUST FAIL pre-wiring: today no spy_returns_fn is passed -> the gate's
    always-0.0 default_oos_alpha (beats-a-flat-return) persists -> below-SPY
    candidates never carry below_spy_alpha."""
    configs = _below_spy_logreturns(3, level=0.0005)
    series_by_ticker = {f"CAND{i}": c for i, c in enumerate(configs)}
    reasoned_pairs = [
        ase.SwapCandidate(incumbent_asset="AAA", candidate_asset=t, rationale="x")
        for t in series_by_ticker
    ]
    spy = _spy_logreturns(0.02)  # SPY clearly above every candidate's level.

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "generate_reasoned_swap_candidates", return_value=reasoned_pairs),
        patch.object(ase, "run_backtest", side_effect=_spy_aware_backtest(series_by_ticker, spy)),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.suggest_swaps(
            symphony_id="prod-spy-swap",
            score_tree=_raw_tree(),
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
            correlation_data={},
            available_assets=list(series_by_ticker),
        )

    reasons = _all_rejection_reasons(result)
    assert any(str(r) == "below_spy_alpha" for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-5, asset_swap_engine): a positive-but-below-SPY "
        "batch through the REAL suggest_swaps produced NO candidate with a "
        f"'below_spy_alpha' rejection_reason. Got reasons: {reasons}"
    )


# ===========================================================================
# N=1 negative invariant (AC-4 edge case) — PBO stays structurally inert at
# K=1 through the REAL propose_operator_swap, regardless of wiring.
# ===========================================================================


def test_ac4_n1_operator_evaluate_never_carries_pbo_veto_even_after_wiring(ase):
    """A single-candidate operator Evaluate must NEVER report rejection_reason
    == 'pbo_veto' — the _PBO_MIN_CONFIGS=2 guard makes PBO structurally None
    at K=1. This is a POSITIVE guard against a wiring implementation that
    naively tries to compute PBO with a single date-keyed config and gets a
    degenerate always-1.0 result (a worse fix would break this)."""
    candidate_series = {d: 0.05 for d in _DATES}
    spy = _spy_logreturns(0.0)  # permissive SPY so PBO-inertness is isolated.

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(
            ase, "run_backtest", side_effect=_spy_aware_backtest({"CAND0": candidate_series}, spy)
        ),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.propose_operator_swap(
            symphony_id="prod-n1-pbo-swap",
            score_tree=_raw_tree(),
            incumbent_asset="AAA",
            candidate_asset="CAND0",
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert "pbo_veto" not in [str(r) for r in reasons], (
        f"AC-4 N=1 GUARD VIOLATED: propose_operator_swap reported a pbo_veto "
        f"rejection_reason at N=1, where PBO must stay structurally None "
        f"(_PBO_MIN_CONFIGS=2). Got reasons: {reasons}"
    )


# ===========================================================================
# N=1 SPY visible-behavior-change (AC-5) — a single candidate that beats zero
# but loses to SPY must WITHHOLD with below_spy_alpha through the REAL
# propose_operator_swap.
# ===========================================================================


def test_ac5_n1_operator_evaluate_beats_zero_loses_to_spy_withholds(ase):
    """MUST FAIL pre-wiring: today the N=1 operator-Evaluate path gates on
    beats-a-flat-0.0%-return only, so a candidate that is positive but below
    SPY currently clears (ADOPT/KEEP_INCUMBENT) instead of being withheld.
    Per PM directive this is the visible production behavior change AC-5 must
    produce even at N=1."""
    candidate_series = {d: 0.0005 for d in _DATES}  # positive, beats 0.0
    spy = _spy_logreturns(0.02)  # SPY clearly above the candidate.

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(
            ase, "run_backtest", side_effect=_spy_aware_backtest({"CAND0": candidate_series}, spy)
        ),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.propose_operator_swap(
            symphony_id="prod-n1-spy-swap",
            score_tree=_raw_tree(),
            incumbent_asset="AAA",
            candidate_asset="CAND0",
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert any(str(r) == "below_spy_alpha" for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-5, N=1 operator Evaluate, asset_swap_engine): "
        "a candidate that beats 0.0% but loses to SPY was not withheld with "
        f"below_spy_alpha. Got reasons: {reasons}"
    )
    assert result.gate_batch.results[0].verdict.decision != "ADOPT_CANDIDATE", (
        "A below-SPY candidate must not be ADOPT_CANDIDATE once the SPY baseline is wired."
    )
