"""Strategy Builder Engine — Phase 2 AI Advisor module.

Proposes new candidate symphonies from scratch (vs engines that mutate live ones):
builds trees from the template library via symphony_schema constructors →
backtests via composer_backtest_client → screens on quantstats metrics →
gates via backtest_gate_engine.evaluate_candidate_batch (FDR) →
persists survivors as advisory observations.

Off-execution-path (never imported from alpha_bot_execution.py).
Advisory-only (is_advisory_only=1 on all persisted observations).
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

import database
from advisors import symphony_schema
from advisors.backtest_gate_engine import (
    HARVEY_LIU_FDR_Q,
    SURVIVOR_OVERFITTING_CAVEAT,
    BacktestCandidate,
    CandidateGateResult,
    GatedBatch,
    evaluate_candidate_batch,
)
from advisors.composer_backtest_client import run_backtest
from analytics import compute_quantstats_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers anywhere in this module
# ---------------------------------------------------------------------------

# Maximum candidates generated per proposal run. PM-ASSUMED: 30 (bounded batch per AC-3).
MAX_CANDIDATES_PER_RUN: int = 30

# Observation type tag written to advisor_observations.
_OBSERVATION_TYPE = "strategy_proposal"

# ScreenConfig default thresholds (PM-ASSUMED values — see exit report).
# Minimum annualized return (fraction scale from quantstats; 0.0 = no minimum).
SCREEN_MIN_CAGR_DEFAULT: float = 0.0
# Minimum Sharpe ratio (0.0 = no minimum).
SCREEN_MIN_SHARPE_DEFAULT: float = 0.0
# Minimum Calmar ratio (0.0 = no minimum).
SCREEN_MIN_CALMAR_DEFAULT: float = 0.0
# Maximum absolute max-drawdown magnitude (fraction scale; 0.50 = allow up to 50% drawdown).
SCREEN_MAX_ABS_DRAWDOWN_DEFAULT: float = 0.50
# Maximum absolute blended drawdown (candidate blended 50/50 with live portfolio returns).
SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT: float = 0.40
# Maximum Pearson correlation with live portfolio daily returns [0, 1].
SCREEN_MAX_CORRELATION_DEFAULT: float = 0.85


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Objective(enum.Enum):
    """Objective enum steering template selection and parameter ranges for a proposal run."""

    diversify = "diversify"
    cut_drawdown = "cut_drawdown"
    lift_risk_adjusted = "lift_risk_adjusted"


@dataclass
class ScreenConfig:
    """Post-gate presentation filter configuration. Defaults are named constants above.

    Applied to gate *survivors* only — never to the gate's input batch.
    Shrinking the gate input would corrupt the FDR correction (AC-3.2).

    A None value for any metric (e.g. insufficient history) causes the
    candidate to fail closed — it is excluded from screened_survivors.
    """

    min_cagr: float = SCREEN_MIN_CAGR_DEFAULT
    min_sharpe: float = SCREEN_MIN_SHARPE_DEFAULT
    min_calmar: float = SCREEN_MIN_CALMAR_DEFAULT
    max_abs_drawdown: float = SCREEN_MAX_ABS_DRAWDOWN_DEFAULT
    max_blended_abs_drawdown: float = SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT
    max_correlation: float = SCREEN_MAX_CORRELATION_DEFAULT


@dataclass
class CandidateInfo:
    """Per-candidate state: tree, template provenance, backtest metrics, and error if backtest failed."""  # noqa: E501

    candidate_id: str
    tree: dict
    template_id: str
    params: dict
    metrics: dict = field(default_factory=dict)
    backtest_error: str | None = None
    data_warnings: list = field(default_factory=list)


@dataclass
class ProposalRun:
    """Result of a propose_strategies call. Never raises — check error field on failure."""

    candidates: list[CandidateInfo]
    gated_batch: GatedBatch
    screened_survivors: list[CandidateGateResult]
    observations_written: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_gate_batch() -> GatedBatch:
    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


def _has_composer_key() -> bool:
    try:
        from alpha_bot_execution import COMPOSER_KEY_ID, COMPOSER_SECRET  # noqa: PLC0415

        return bool(COMPOSER_KEY_ID and COMPOSER_SECRET)
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Template library — 7 templates
# ---------------------------------------------------------------------------


def equal_weight_basket(tickers: list[str], name: str = "Equal Weight Basket") -> dict:
    """T1: equal-weight allocation over the given tickers."""
    assets = [symphony_schema.make_asset(t) for t in tickers]
    wt = symphony_schema.make_weight_equal(assets)
    return symphony_schema.make_root(name, "daily", [wt])


def specified_weight_basket(
    weighted_tickers: list[tuple[str, float]],
    name: str = "Specified Weight Basket",
) -> dict:
    """T2: specified-weight allocation over the given (ticker, weight) pairs."""
    children_with_weights = [(symphony_schema.make_asset(t), w) for t, w in weighted_tickers]
    wt = symphony_schema.make_weight_specified(children_with_weights)
    return symphony_schema.make_root(name, "daily", [wt])


def inverse_vol_basket(tickers: list[str], name: str = "Inverse Vol Basket") -> dict:
    """T3: inverse-volatility weighted allocation over the given tickers."""
    assets = [symphony_schema.make_asset(t) for t in tickers]
    wt = symphony_schema.make_inverse_vol(assets)
    return symphony_schema.make_root(name, "daily", [wt])


def trend_switch(
    signal_ticker: str,
    ma_window: int,
    risk_on_tickers: list[str],
    risk_off_tickers: list[str],
    name: str = "Trend Switch",
) -> dict:
    """T4: trend-following switch: risk-on when signal > MA, else risk-off."""
    # Condition: current-price(signal) > moving-average-price(signal, window)
    lhs = symphony_schema.make_indicator("current-price", signal_ticker, window=1)
    rhs_ind = symphony_schema.make_indicator(
        "moving-average-price", signal_ticker, window=ma_window
    )
    cond = symphony_schema.make_condition(lhs, "gt", signal_ticker, rhs_indicator=rhs_ind)

    # Risk-on: equal-weight over risk_on_tickers
    risk_on_assets = [symphony_schema.make_asset(t) for t in risk_on_tickers]
    risk_on_wt = symphony_schema.make_weight_equal(risk_on_assets)

    # Risk-off: equal-weight over risk_off_tickers
    risk_off_assets = [symphony_schema.make_asset(t) for t in risk_off_tickers]
    risk_off_wt = symphony_schema.make_weight_equal(risk_off_assets)

    if_node = symphony_schema.make_if(cond, then_children=[risk_on_wt], else_children=[risk_off_wt])
    return symphony_schema.make_root(name, "daily", [if_node])


def rsi_rotation(
    signal_ticker: str,
    rsi_window: int,
    threshold: float,
    overbought_tickers: list[str],
    neutral_tickers: list[str],
    name: str = "RSI Rotation",
) -> dict:
    """T5: RSI-based rotation — overbought when RSI > threshold, else neutral.

    The indicator function token is ``relative-strength-index`` (the full canonical
    name per grammar doc §4.1).  Using the abbreviation ``rsi`` is a lint warning
    (unknown fn) and must NOT appear as a standalone fn value in any tree this
    template emits.
    """
    # Condition: relative-strength-index(signal, window) > threshold (fixed value)
    lhs = symphony_schema.make_indicator(
        "relative-strength-index", signal_ticker, window=rsi_window
    )
    cond = symphony_schema.make_condition(lhs, "gt", threshold)

    overbought_assets = [symphony_schema.make_asset(t) for t in overbought_tickers]
    overbought_wt = symphony_schema.make_weight_equal(overbought_assets)

    neutral_assets = [symphony_schema.make_asset(t) for t in neutral_tickers]
    neutral_wt = symphony_schema.make_weight_equal(neutral_assets)

    if_node = symphony_schema.make_if(
        cond, then_children=[overbought_wt], else_children=[neutral_wt]
    )
    return symphony_schema.make_root(name, "daily", [if_node])


def momentum_top_n(universe: list[str], n: int, window: int, name: str = "Momentum Top N") -> dict:
    """T6: select top-N assets by cumulative return over the given window."""
    assets = [symphony_schema.make_asset(t) for t in universe]
    # select-fn "top" is the VERIFIED-LOCAL Composer API value (grammar §3.5).
    # sort-by-fn "cumulative-return" is used per Phase-2 contract mandate (T6).
    # UNVERIFIED as sort-by-fn in grammar fixtures (verified as indicator fn only).
    # If Composer API rejects, consult grammar doc §4.2 and composer-api-researcher.
    flt = symphony_schema.make_filter(
        select_fn="top",
        select_n=n,
        sort_by_fn="cumulative-return",
        children=assets,
        window=window,
    )
    return symphony_schema.make_root(name, "daily", [flt])


def low_vol_floor(universe: list[str], n: int, window: int, name: str = "Low Vol Floor") -> dict:
    """T7: select bottom-N assets by standard-deviation-return over the given window."""
    assets = [symphony_schema.make_asset(t) for t in universe]
    # select-fn "bottom" is the VERIFIED-LOCAL Composer API value (grammar §3.5).
    # sort-by-fn "standard-deviation-return" is used per Phase-2 contract mandate (T7).
    # UNVERIFIED as sort-by-fn in grammar fixtures (verified as indicator fn only).
    # If Composer API rejects, consult grammar doc §4.2 and composer-api-researcher.
    flt = symphony_schema.make_filter(
        select_fn="bottom",
        select_n=n,
        sort_by_fn="standard-deviation-return",
        children=assets,
        window=window,
    )
    return symphony_schema.make_root(name, "daily", [flt])


# ---------------------------------------------------------------------------
# Objective-directed candidate generation
# ---------------------------------------------------------------------------


def _generate_candidate_trees(
    objective: Objective,
    universe: list[str],
) -> list[CandidateInfo]:
    """Generate up to MAX_CANDIDATES_PER_RUN objective-directed candidates."""
    candidates: list[CandidateInfo] = []

    if not universe:
        return candidates

    # Use at most 10 tickers to keep tree sizes manageable
    tickers = universe[:10]

    if objective == Objective.diversify:
        # T1 equal-weight, T3 inverse-vol, T6 momentum at multiple windows
        candidates.append(
            CandidateInfo(
                candidate_id="diversify:T1:equal_weight",
                tree=equal_weight_basket(tickers, name="Diversify Equal Weight"),
                template_id="T1",
                params={"tickers": tickers},
            )
        )
        candidates.append(
            CandidateInfo(
                candidate_id="diversify:T3:inverse_vol",
                tree=inverse_vol_basket(tickers, name="Diversify Inverse Vol"),
                template_id="T3",
                params={"tickers": tickers},
            )
        )
        if len(tickers) >= 3:
            for w in [63, 126]:
                candidates.append(
                    CandidateInfo(
                        candidate_id=f"diversify:T6:momentum_w{w}",
                        tree=momentum_top_n(
                            tickers,
                            n=max(2, len(tickers) // 2),
                            window=w,
                            name=f"Diversify Momentum {w}d",
                        ),
                        template_id="T6",
                        params={
                            "tickers": tickers,
                            "n": max(2, len(tickers) // 2),
                            "window": w,
                        },
                    )
                )

    elif objective == Objective.cut_drawdown:
        # T7 low-vol, T3 inverse-vol, T4 trend-switch (if enough tickers)
        candidates.append(
            CandidateInfo(
                candidate_id="cut_dd:T3:inverse_vol",
                tree=inverse_vol_basket(tickers, name="Cut DD Inverse Vol"),
                template_id="T3",
                params={"tickers": tickers},
            )
        )
        if len(tickers) >= 2:
            n_low = max(1, len(tickers) // 3 + 1)
            for w in [20, 60]:
                candidates.append(
                    CandidateInfo(
                        candidate_id=f"cut_dd:T7:low_vol_w{w}",
                        tree=low_vol_floor(
                            tickers,
                            n=n_low,
                            window=w,
                            name=f"Cut DD Low Vol {w}d",
                        ),
                        template_id="T7",
                        params={"tickers": tickers, "n": n_low, "window": w},
                    )
                )
        if len(tickers) >= 4:
            mid = len(tickers) // 2
            candidates.append(
                CandidateInfo(
                    candidate_id="cut_dd:T4:trend_switch_200",
                    tree=trend_switch(
                        tickers[0],
                        ma_window=200,
                        risk_on_tickers=tickers[:mid],
                        risk_off_tickers=tickers[mid:],
                        name="Cut DD Trend Switch 200d",
                    ),
                    template_id="T4",
                    params={
                        "signal_ticker": tickers[0],
                        "ma_window": 200,
                        "risk_on": tickers[:mid],
                        "risk_off": tickers[mid:],
                    },
                )
            )

    elif objective == Objective.lift_risk_adjusted:
        # T6 momentum, T5 RSI, T2 specified-weight top picks
        if len(tickers) >= 3:
            for w in [21, 63, 126]:
                n_top = max(1, len(tickers) // 3)
                candidates.append(
                    CandidateInfo(
                        candidate_id=f"lift_ra:T6:momentum_w{w}",
                        tree=momentum_top_n(
                            tickers,
                            n=n_top,
                            window=w,
                            name=f"Lift RA Momentum {w}d",
                        ),
                        template_id="T6",
                        params={"tickers": tickers, "n": n_top, "window": w},
                    )
                )
        if len(tickers) >= 2:
            candidates.append(
                CandidateInfo(
                    candidate_id="lift_ra:T5:rsi_rotation_14_70",
                    tree=rsi_rotation(
                        tickers[0],
                        rsi_window=14,
                        threshold=70.0,
                        overbought_tickers=tickers[: len(tickers) // 2],
                        neutral_tickers=tickers[len(tickers) // 2 :],
                        name="Lift RA RSI Rotation",
                    ),
                    template_id="T5",
                    params={
                        "signal_ticker": tickers[0],
                        "rsi_window": 14,
                        "threshold": 70.0,
                    },
                )
            )
        # T2 specified-weight (equal split for simplicity)
        n = len(tickers)
        if n >= 2:
            w_each = 100.0 / n
            weighted = [(t, w_each) for t in tickers]
            candidates.append(
                CandidateInfo(
                    candidate_id="lift_ra:T2:specified_weight",
                    tree=specified_weight_basket(weighted, name="Lift RA Specified Weight"),
                    template_id="T2",
                    params={"tickers": tickers, "weights": [w_each] * n},
                )
            )

    return candidates[:MAX_CANDIDATES_PER_RUN]


# ---------------------------------------------------------------------------
# Screen helpers
# ---------------------------------------------------------------------------


def _pearson_corr(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation. Returns 0.0 on degenerate input."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys, strict=True)) / n
    var_x = sum((a - mean_x) ** 2 for a in xs) / n
    var_y = sum((b - mean_y) ** 2 for b in ys) / n
    denom = (var_x * var_y) ** 0.5
    return cov / denom if denom > 1e-12 else 0.0


def _passes_screens(
    metrics: dict,
    live_returns: list[float],
    screen_config: ScreenConfig,
    returns_pct: list[float],
) -> bool:
    """Return True iff the candidate passes all configured screens.

    None metric (insufficient data) → screen fails closed (candidate excluded).
    Blended drawdown: candidate returns blended 50/50 with live_returns.
    Correlation: Pearson vs live portfolio daily returns.
    """
    # min_cagr
    cagr = metrics.get("annualized_return")
    if cagr is None or cagr < screen_config.min_cagr:
        return False

    # min_sharpe
    sharpe = metrics.get("sharpe")
    if sharpe is None or sharpe < screen_config.min_sharpe:
        return False

    # min_calmar
    calmar = metrics.get("calmar")
    if calmar is None or calmar < screen_config.min_calmar:
        return False

    # max_abs_drawdown (max_drawdown <= 0 from quantstats; abs converts)
    mdd = metrics.get("max_drawdown")
    if mdd is None:
        return False
    if mdd > 0:
        logger.warning(
            "_passes_screens: max_drawdown > 0 from analytics layer (expected <= 0); "
            "treating as magnitude"
        )
    if abs(mdd) > screen_config.max_abs_drawdown:
        return False

    # max_blended_abs_drawdown: blend candidate and live returns 50/50 (tail-aligned)
    if live_returns and returns_pct:
        n = min(len(live_returns), len(returns_pct))
        blended = [
            (r + lv) * 0.5
            for r, lv in zip(returns_pct[-n:], live_returns[-n:], strict=True)
        ]
        blended_metrics = compute_quantstats_metrics(blended)
        blended_mdd = blended_metrics.get("max_drawdown")
        if blended_mdd is None or abs(blended_mdd) > screen_config.max_blended_abs_drawdown:
            return False

    # max_correlation
    if live_returns and returns_pct:
        corr = abs(_pearson_corr(returns_pct, live_returns))
        if corr > screen_config.max_correlation:
            return False

    return True


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------


def _persist_survivor(
    symphony_id: str,
    info: CandidateInfo,
    gate_result: CandidateGateResult,
    n_candidates: int,
) -> None:
    """Persist an ADOPT_CANDIDATE survivor as an advisory observation."""
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        symphony_id=symphony_id,
        subject_type="strategy_proposal",
        subject_id=info.candidate_id,
        verdict="ADOPT_CANDIDATE",
        is_advisory_only=1,
        raw_response={
            "objective": info.params.get("objective", ""),
            "template_id": info.template_id,
            "params": info.params,
            "rules_text": symphony_schema.render_rules_text(info.tree),
            "metrics": info.metrics,
            "gate_decision": gate_result.verdict.decision,
            "winner_p_adj": gate_result.winner_p_adj,
            "n_candidates": n_candidates,
            "caveats": list(gate_result.caveats) + [SURVIVOR_OVERFITTING_CAVEAT],
        },
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def propose_strategies(
    objective: Objective,
    universe: list[str],
    screen_config: ScreenConfig,
    live_returns: list[float],
    symphony_id: str = "",
    *,
    incumbent_oos_alpha: float = 0.0,
    default_oos_alpha: float = 0.0,
) -> ProposalRun:
    """Propose new candidate symphonies from scratch.

    Never raises — all exceptions are caught and surfaced as ``ProposalRun.error``.

    Args:
        objective: Steers template selection and parameter ranges.
        universe: Ticker symbols to draw candidates from (at most 10 are used).
        screen_config: Post-gate presentation filter; applied to gate survivors only
            (never to the gate input — see AC-3.2 FDR integrity).
        live_returns: Chronological daily returns of the live portfolio in
            percent scale (e.g. 0.5 means +0.5%).  Used for blended-drawdown and
            Pearson-correlation screens.  May be empty; screens that require it are
            skipped when it is.
        symphony_id: Composer symphony ID that observations are keyed to.
            Defaults to ``""`` (advisory runs not tied to a specific symphony).
        incumbent_oos_alpha: Out-of-sample alpha of the incumbent strategy, passed
            directly to ``evaluate_candidate_batch`` for the BHY FDR gate.
        default_oos_alpha: Fallback OOS alpha used by the gate when no incumbent
            alpha is available.

    Returns:
        ProposalRun where:
        - ``candidates`` contains only successfully-backtested ``CandidateInfo``
          objects (candidates whose backtest errored are omitted).
        - ``gated_batch.n_candidates`` equals ``len(candidates)`` (the FDR gate
          input count — never the total attempted or the post-screen count).
        - ``screened_survivors`` is a subset of ``gated_batch.survivors``.
        - ``error`` is non-None when a top-level exception occurred.

    FDR integrity: evaluate_candidate_batch receives ALL successfully backtested
    candidates (AC-3.2). Screens apply only to gate survivors (post-gate presentation).
    """
    try:
        if not _has_composer_key():
            return ProposalRun(
                candidates=[],
                gated_batch=_empty_gate_batch(),
                screened_survivors=[],
                observations_written=0,
                error="Composer API key not configured",
            )

        # Step 1: Generate candidate trees (objective-directed, bounded)
        candidate_infos = _generate_candidate_trees(objective, universe)

        if not candidate_infos:
            return ProposalRun(
                candidates=[],
                gated_batch=_empty_gate_batch(),
                screened_survivors=[],
                observations_written=0,
            )

        # Step 2: Backtest each candidate (sequential, 1 req/s via client pacing)
        # One failure never aborts the batch (AC-X5 pattern).
        bt_candidates: list[BacktestCandidate] = []
        returns_by_id: dict[str, list[float]] = {}

        for info in candidate_infos:
            try:
                result = run_backtest(info.tree, symphony_id=symphony_id)
                if result.error:
                    info.backtest_error = f"backtest failed: {result.error}"
                    info.data_warnings = result.data_warnings
                    continue
                # Convert log returns → pct (EXACTLY as in asset_swap_engine.py:577)
                returns_pct = [r * 100.0 for r in result.daily_returns.values()]
                # Compute quantstats metrics (pct-scale in → fraction-scale out)
                info.metrics = compute_quantstats_metrics(returns_pct)
                info.data_warnings = result.data_warnings
                returns_by_id[info.candidate_id] = returns_pct
                bt_candidates.append(
                    BacktestCandidate(
                        candidate_id=info.candidate_id,
                        daily_returns_pct=returns_pct,
                        candidate_params={},
                        incumbent_params={},
                        theory_prior_params={},
                        nn1_compliant=True,
                        purge_integrity_ok=True,
                    )
                )
            except Exception as exc:
                info.backtest_error = str(exc)

        # Step 3: FDR gate — full batch, no pre-filtering (AC-3.2)
        gate_batch = evaluate_candidate_batch(
            bt_candidates,
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
        )

        # Step 4: Screens apply to gate survivors ONLY (post-gate presentation filter)
        screened_survivors: list[CandidateGateResult] = []
        for gate_result in gate_batch.survivors:
            cid = gate_result.candidate_id
            returns_pct = returns_by_id.get(cid, [])
            info = next((i for i in candidate_infos if i.candidate_id == cid), None)
            if info is None:
                continue
            if _passes_screens(info.metrics, live_returns, screen_config, returns_pct):
                screened_survivors.append(gate_result)

        # Step 5: Persist survivors (is_advisory_only=1)
        obs_written = 0
        for gate_result in screened_survivors:
            cid = gate_result.candidate_id
            info = next((i for i in candidate_infos if i.candidate_id == cid), None)
            if info is None:
                continue
            try:
                _persist_survivor(symphony_id, info, gate_result, len(bt_candidates))
                obs_written += 1
            except Exception:
                logger.warning(
                    "propose_strategies: failed to persist survivor %s",
                    cid,
                    exc_info=True,
                )

        # result.candidates = only successfully-backtested ones (those with metrics)
        # gated_batch.n_candidates must equal len(bt_candidates) which equals
        # the number of successfully-backtested candidates.
        # But the test asserts result.candidates == gated_batch.n_candidates,
        # so result.candidates must only contain successful ones.
        successful_candidates = [info for info in candidate_infos if info.backtest_error is None]

        return ProposalRun(
            candidates=successful_candidates,
            gated_batch=gate_batch,
            screened_survivors=screened_survivors,
            observations_written=obs_written,
        )

    except Exception as exc:
        logger.exception("propose_strategies: unexpected error")
        return ProposalRun(
            candidates=[],
            gated_batch=_empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
            error=str(exc),
        )
