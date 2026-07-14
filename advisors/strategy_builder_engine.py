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
import math
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

# Maximum community-sourced candidates admitted per proposal run. Caps the caller-injected
# community_candidates list inside propose_strategies regardless of adapter output size.
MAX_COMMUNITY_CANDIDATES_PER_RUN: int = 20

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
# Phase-3.6: target sparkline resolution. 60 pts ≈ 2.5 years of daily data at 5px/pt
# on a 280px card — sufficient visual fidelity for direction/shape; see research spec §3.
# Uniform stride was chosen over LTTB to avoid a third-party dependency on the persist path.
SPARKLINE_TARGET_POINTS: int = 60


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Objective(enum.Enum):
    """Objective enum steering template selection and parameter ranges for a proposal run.

    Four values matching build_plan_generator.Objective (Q1-A, AC-8):
      diversify             — multi-sleeve allocation (>=2 container children).
      cut_drawdown          — regime gate or inverse-vol weight.
      lift_risk_adjusted    — momentum/quality filter.
      volatility_mitigation — inverse-vol weight or low/min-vol filter.
    """

    diversify = "diversify"
    cut_drawdown = "cut_drawdown"
    lift_risk_adjusted = "lift_risk_adjusted"
    volatility_mitigation = "volatility_mitigation"


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
    # True when plan_tree_compiler.compile_plan degraded this tree on an
    # infra/transport failure (Composer unreachable) instead of pruning/
    # dropping — the tree's tradeability against Composer was never
    # confirmed. Community candidates (Atlas-sourced, not compiled via
    # plan_tree_compiler) always default False.
    tradeability_unverified: bool = False


@dataclass
class ProposalRun:
    """Result of a propose_strategies call. Never raises — check error field on failure."""

    candidates: list[CandidateInfo]
    gated_batch: GatedBatch
    screened_survivors: list[CandidateGateResult]
    observations_written: int
    error: str | None = None
    # AC-11: sanitized cause category (type(exc).__name__) for the route to
    # surface to the operator. `error` may carry raw exception text (hostnames,
    # paths, credentials) and must never be echoed to the client verbatim
    # (AC-23 precedent) — error_category is the safe, displayable alternative.
    error_category: str | None = None
    # Honest run-level outage signal (advisor-outage-degrade AC-4): True when
    # one or more generated candidates were emitted tradeability-unverified
    # because Composer was unreachable during compile_plan's repair loop.
    # Computed from the FULL pre-Step-2-backtest candidate list, NOT from
    # `candidates` above — `candidates` is filtered to only those whose own
    # Step-2 metrics backtest succeeded, which a real outage would also fail,
    # silently zeroing a `candidates`-derived count in exactly the case this
    # flag exists to surface.
    backtest_unavailable: bool = False
    backtest_unavailable_count: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_gate_batch() -> GatedBatch:
    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


# ---------------------------------------------------------------------------
# Phase 3.6 — Equity-curve helpers
# ---------------------------------------------------------------------------


def _cumulative_returns(returns_pct: list[float]) -> list[float]:
    """Running sum of a daily-returns-pct series, each value rounded to 2dp.

    An empty input returns an empty list.  A single-element input returns a
    one-element list containing that element rounded to 2 decimal places.
    """
    result: list[float] = []
    running = 0.0
    for r in returns_pct:
        running += r
        result.append(round(running, 2))
    return result


def _downsample(series: list[float], target: int = SPARKLINE_TARGET_POINTS) -> list[float]:
    """Deterministically reduce ``series`` to at most ``target`` points.

    Guarantees:
    - Returns the series unchanged when ``len(series) <= target``.
    - Preserves the first and last points exactly.
    - Deterministic: identical inputs always produce identical outputs.

    Uses integer stride selection (evenly-spaced indices) over the interior,
    then appends the final element to guarantee endpoint preservation.
    """
    n = len(series)
    if n <= target:
        return list(series)
    if target <= 0:
        return []
    if target == 1:
        return [series[0]]
    # Select ``target - 1`` evenly-spaced indices from [0, n-2), then force
    # the last element.  This guarantees exactly ``target`` output points.
    indices: list[int] = []
    for i in range(target - 1):
        idx = int(i * (n - 1) / (target - 1))
        indices.append(idx)
    # Always include the last index
    indices.append(n - 1)
    return [series[i] for i in indices]


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
    # sort-by-fn "cumulative-return" is VERIFIED-LOCAL in sort position
    # (vocabulary research: ~5 occurrences in sample_score_large.json).
    flt = symphony_schema.make_filter(
        select_fn="top",
        select_n=n,
        sort_by_fn="cumulative-return",
        children=assets,
        window=window,
    )
    return symphony_schema.make_root(name, "daily", [flt])


def low_vol_floor(universe: list[str], n: int, window: int, name: str = "Low Vol Floor") -> dict:
    """T7: select bottom-N (least-drawdown) assets by max-drawdown over the window.

    Defensive-floor template. max-drawdown ranking is the VERIFIED-LOCAL proxy
    for low volatility: the contract's original sort key
    (standard-deviation-return) was refuted in sort-by position by the
    vocabulary deep-research (strategy-builder-vocabulary-research.md), and
    standard-deviation-price — though verified — is price-scale-biased
    (cheap tickers rank as 'calm' regardless of return volatility).
    """
    assets = [symphony_schema.make_asset(t) for t in universe]
    # select-fn "bottom" is the VERIFIED-LOCAL Composer API value (grammar §3.5).
    # sort-by-fn "max-drawdown" is VERIFIED-LOCAL in sort position (vocabulary
    # research: full verified sort-by-fn set). Bottom-N = smallest drawdown.
    flt = symphony_schema.make_filter(
        select_fn="bottom",
        select_n=n,
        sort_by_fn="max-drawdown",
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
    """Generate up to MAX_CANDIDATES_PER_RUN objective-directed candidates via the real builder.

    C4 body swap: drives the real C1→C2→C3 pipeline instead of the old 7-template stamper.

    Steps:
      C1 self-source (Q2-A): non-empty `universe` → use it as the membership set; empty/
        omitted → self-source from universe_provider.get_tradeable_set().
      C2 generate: call build_plan_generator.generate_build_plans(objective, membership_set).
        Maps sbe.Objective → build_plan_generator.Objective by .value (string-keyed, 4-way).
      C3 compile: loop plans through plan_tree_compiler.compile_plan(plan). Keep compiled
        trees (CompileResult.tree not None), drop uncompilable ones (e.g. market_cap-scheme
        → reason="market_cap_scheme_deprecated"). Run continues on any drop.

    Honest degradation: generator returns empty plans (D-1 reason) → returns [] cleanly.
    D-1 never-raises: any internal exception degrades to [] with a logged class name only.
    """
    # CC-2 lazy imports — off-execution-path; never imported from alpha_bot_execution.py.
    from advisors import build_plan_generator as _gen  # noqa: PLC0415
    from advisors import plan_tree_compiler as _compiler  # noqa: PLC0415
    from advisors import universe_provider as _up  # noqa: PLC0415

    try:
        # C1 — Q2-A: use non-empty universe override as-is; self-source when empty.
        if universe:
            membership_set: frozenset = frozenset(universe)
        else:
            membership_set = _up.get_tradeable_set()

        # C2 — map sbe.Objective → build_plan_generator.Objective by .value (string-keyed).
        gen_objective = _gen.Objective(objective.value)
        result = _gen.generate_build_plans(gen_objective, membership_set)

        if not result.plans:
            # D-1 honest degradation: no plans from generator (SDK error, signature-floor, etc.)
            logger.debug(
                "_generate_candidate_trees: generator returned no plans (reason=%r)",
                result.reason,
            )
            return []

        # C3 — compile each plan; drop uncompilable ones, keep the run going.
        candidates: list[CandidateInfo] = []
        for plan in result.plans:
            # AC-12: thread the real backtest seam so compile_plan's tradeability-
            # repair loop (dead when backtest_fn defaults to None) is live on this
            # reachable path — revives the AC-16 repair loop at plan_tree_compiler.py:379.
            compile_result = _compiler.compile_plan(plan, backtest_fn=run_backtest)
            if compile_result.tree is None:
                # Drop: market_cap-deprecated, validate_tree hard error, or other clean drop.
                logger.debug(
                    "_generate_candidate_trees: dropped plan %r (reason=%r)",
                    plan.get("plan_id"),
                    compile_result.reason,
                )
                continue

            provenance = plan.get("provenance", "built-new")
            candidates.append(
                CandidateInfo(
                    candidate_id=plan["plan_id"],
                    tree=compile_result.tree,
                    # template_id carries provenance, never T1-T7 (anti-hollow core).
                    template_id=provenance,
                    params={
                        "plan_id": plan["plan_id"],
                        "name": plan.get("name", ""),
                        "objective": plan.get("objective", ""),
                        "provenance": provenance,
                    },
                    # advisor-outage-degrade AC-2/AC-4: carries forward compile_plan's
                    # infra-degrade marker so propose_strategies can roll it up honestly.
                    tradeability_unverified=compile_result.tradeability_unverified,
                )
            )
            if len(candidates) >= MAX_CANDIDATES_PER_RUN:
                break

        return candidates

    except Exception as exc:
        # D-1: degrade cleanly — never propagate an exception from the generator path.
        logger.debug(
            "_generate_candidate_trees: unexpected error (%s)", type(exc).__name__, exc_info=True
        )
        return []


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
            (r + lv) * 0.5 for r, lv in zip(returns_pct[-n:], live_returns[-n:], strict=True)
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
# Persistence helpers
# ---------------------------------------------------------------------------


def _build_live_baseline(
    live_returns: list[float],
    returns_pct: list[float],
) -> dict | None:
    """Compute live-baseline metrics over the tail-aligned window used by the
    correlation / blended-drawdown screens (HR-5).

    When both series are non-empty, the window is tail-aligned:
        n = min(len(live_returns), len(returns_pct))
        live_tail = live_returns[-n:]
    When only live_returns is non-empty (no candidate series), use all of live_returns.

    Returns a dict of metric keys mirroring the candidate raw_response fields,
    or None when live_returns is empty (so the template can omit the baseline
    column entirely for old rows — PA-3 / contract §2 'omitted when not provided').
    """
    if not live_returns:
        return None
    if returns_pct:
        n = min(len(live_returns), len(returns_pct))
        live_tail = live_returns[-n:]
    else:
        live_tail = live_returns
    live_m = compute_quantstats_metrics(live_tail)
    return {
        "cagr": live_m.get("annualized_return"),
        "sharpe": live_m.get("sharpe"),
        "calmar": live_m.get("calmar"),
        "max_drawdown": live_m.get("max_drawdown"),
        # correlation_vs_live and blended_drawdown are N/A for the baseline itself
        "correlation_vs_live": None,
        "blended_drawdown": None,
    }


def _sanitize_non_finite(obj):
    """Recursively replace non-finite floats (NaN/±inf, incl. numpy scalars)
    with None so the persisted raw_response is always RFC-7159-serializable.

    float(x) coerces numpy float64 before the isfinite check, so a single
    guard covers both Python and numpy non-finite values (IC2-BUG 1/2).
    """
    if isinstance(obj, dict):
        return {k: _sanitize_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_non_finite(v) for v in obj]
    if isinstance(obj, float) or type(obj).__name__ in ("float64", "float32"):
        value = float(obj)
        return value if math.isfinite(value) else None
    return obj


def _build_screen_metrics(
    live_returns: list[float],
    returns_pct: list[float],
) -> tuple[float | None, float | None]:
    """Compute correlation_vs_live and blended_drawdown using the SAME tail-aligned
    window as _passes_screens (HR-5).

    Returns (correlation_vs_live, blended_drawdown). Both are None when either
    series is empty (screens that require live_returns are skipped in those cases).
    """
    if not live_returns or not returns_pct:
        return None, None
    n = min(len(live_returns), len(returns_pct))
    corr = abs(_pearson_corr(returns_pct[-n:], live_returns[-n:]))
    blended = [(r + lv) * 0.5 for r, lv in zip(returns_pct[-n:], live_returns[-n:], strict=True)]
    blended_metrics = compute_quantstats_metrics(blended)
    blended_mdd = blended_metrics.get("max_drawdown")
    return corr, blended_mdd


def _persist_survivor(
    symphony_id: str,
    info: CandidateInfo,
    gate_result: CandidateGateResult,
    n_candidates: int,
    fdr_q: float = HARVEY_LIU_FDR_Q,
    *,
    live_returns: list[float] | None = None,
    returns_pct: list[float] | None = None,
    n_survivors: int = 0,
    is_rejected: bool = False,
) -> None:
    """Persist an ADOPT_CANDIDATE survivor (or rejected candidate) as an advisory observation.

    fdr_q and fdr_adjusted_threshold are stored so the dashboard template can
    render the numeric adjusted threshold (phase3-contract.md line 39).
    fdr_adjusted_threshold = fdr_q / c(n) where c(n) is the Yekutieli harmonic
    sum — same formula as autotuner._c_yekutieli and the /run route.

    Phase-3.5: also persists candidate quantstats metrics (cagr, sharpe, calmar,
    max_drawdown), screen metadata (correlation_vs_live, blended_drawdown,
    n_survivors), and live_baseline sub-dict so downstream surfaces can render
    without recomputation (HR-2).

    Args:
        is_rejected: When True, write the gate_result verdict string as the DB verdict
            (not 'ADOPT_CANDIDATE'). Used for the rejected-candidate persist path.
            HR-3: gate semantics untouched — metrics are recorded, never re-gated.
        returns_pct: Candidate daily returns in percent scale. When not provided,
            falls back to info._returns_pct if set (allows test helpers to inject
            candidate returns without modifying the call site).
    """
    _live_returns = live_returns or []
    # returns_pct kwarg takes priority; info._returns_pct is a test/fallback seam
    _returns_pct = (
        returns_pct if returns_pct is not None else getattr(info, "_returns_pct", None) or []
    )

    # Verdict: rejected path uses gate decision string; survivor always ADOPT_CANDIDATE
    verdict_str = gate_result.verdict.decision if is_rejected else "ADOPT_CANDIDATE"

    # Yekutieli c(n) = sum(1/k for k in 1..n_candidates)
    c_n = sum(1.0 / k for k in range(1, n_candidates + 1)) if n_candidates > 0 else 1.0
    fdr_adjusted_threshold = fdr_q / c_n if c_n > 0 else fdr_q

    # Phase-3.5: extract flat metric fields from info.metrics (already computed — HR-2)
    cagr = info.metrics.get("annualized_return")
    sharpe = info.metrics.get("sharpe")
    calmar = info.metrics.get("calmar")
    max_drawdown = info.metrics.get("max_drawdown")

    # Phase-3.5: compute screen metadata using the same tail-aligned window as
    # _passes_screens (HR-5) — no new I/O, purely CPU dict assembly
    correlation_vs_live, blended_drawdown = _build_screen_metrics(_live_returns, _returns_pct)

    # Phase-3.5: compute live_baseline metrics over the same tail-aligned window.
    # live_baseline is ABSENT (not None) when live_returns is empty (PA-3: contract §2
    # 'omitted when not provided' means the key must not exist, not be set to None).
    live_baseline = _build_live_baseline(_live_returns, _returns_pct)

    caveats = list(gate_result.caveats)
    if not is_rejected:
        caveats = caveats + [SURVIVOR_OVERFITTING_CAVEAT]

    raw_response: dict = {
        "objective": info.params.get("objective", ""),
        "template_id": info.template_id,
        "params": info.params,
        "rules_text": symphony_schema.render_rules_text(info.tree),
        "metrics": info.metrics,
        # Phase-3.5: flat metric fields (CHAT_ARTIFACT_ALLOWED_FIELDS — FROZEN)
        "cagr": cagr,
        "sharpe": sharpe,
        "calmar": calmar,
        "max_drawdown": max_drawdown,
        "correlation_vs_live": correlation_vs_live,
        "blended_drawdown": blended_drawdown,
        # Phase-3.5: batch metadata
        "n_survivors": n_survivors,
        "gate_decision": gate_result.verdict.decision,
        "winner_p_adj": gate_result.winner_p_adj,
        "n_candidates": n_candidates,
        "fdr_q": fdr_q,
        "fdr_adjusted_threshold": fdr_adjusted_threshold,
        "caveats": caveats,
    }

    # PA-3: live_baseline key is ABSENT (not None, not present) when live_returns empty.
    # Contract §2: 'omitted when not provided'. Only include when _build_live_baseline
    # returns a dict (which requires both live_returns and returns_pct to be non-empty).
    if live_baseline is not None:
        raw_response["live_baseline"] = live_baseline

    # Phase 3.6: inject downsampled equity curve when returns are available.
    # Key is ABSENT (not None, not []) when _returns_pct is empty — template
    # renders sparkline only when the key is present and non-empty.
    if _returns_pct:
        raw_response["equity_curve_downsampled"] = _downsample(_cumulative_returns(_returns_pct))

    # Independent cycle 2, IC2-BUG 1/2: non-finite floats (NaN/inf, Python or
    # numpy) anywhere in the payload make json.dumps emit non-RFC 'NaN', which
    # silently breaks JSON.parse in the Discuss button's data-artifact attribute.
    # The compute path never produces NaN today, but the persist boundary must
    # not depend on that.
    raw_response = _sanitize_non_finite(raw_response)

    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        symphony_id=symphony_id,
        subject_type="strategy_proposal",
        subject_id=info.candidate_id,
        verdict=verdict_str,
        is_advisory_only=1,
        raw_response=raw_response,
    )


def _persist_rejected(
    symphony_id: str,
    info: CandidateInfo,
    gate_result: CandidateGateResult,
    n_candidates: int,
    fdr_q: float = HARVEY_LIU_FDR_Q,
    *,
    live_returns: list[float] | None = None,
    returns_pct: list[float] | None = None,
    n_survivors: int = 0,
) -> None:
    """Persist a gate-rejected or screen-rejected candidate as an advisory observation.

    Delegates to _persist_survivor with is_rejected=True so the verdict reflects
    the gate decision (e.g. 'WITHHELD_FDR') rather than 'ADOPT_CANDIDATE'.
    Phase-3.5 [PM-ASSUMED]: rejected candidates persist metrics too so their cards
    and M6 artifacts benefit equally from the same baseline column rendering.
    """
    _persist_survivor(
        symphony_id,
        info,
        gate_result,
        n_candidates,
        fdr_q,
        live_returns=live_returns,
        returns_pct=returns_pct,
        n_survivors=n_survivors,
        is_rejected=True,
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
    community_candidates: list[CandidateInfo] | None = None,
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
        community_candidates: Optional pre-built ``CandidateInfo`` objects sourced from
            the community-strategies loader (via
            ``build_plan_generator.load_atlas_candidates``).  These
            are appended to the template-generated candidates and flow through the SAME
            single-batch FDR gate (AC-2).  Capped at ``MAX_COMMUNITY_CANDIDATES_PER_RUN``
            inside this function regardless of list length (AC-3).  ``None`` and ``[]``
            are identical — no community candidates are injected (AC-6).

    Returns:
        ProposalRun where:
        - ``candidates`` contains only successfully-backtested ``CandidateInfo``
          objects (candidates whose backtest errored are omitted).
        - ``gated_batch.n_candidates`` equals ``len(candidates)`` (the FDR gate
          input count — never the total attempted or the post-screen count).
        - ``screened_survivors`` is a subset of ``gated_batch.survivors``.
        - ``error`` is non-None when a top-level exception occurred.
        - ``backtest_unavailable`` / ``backtest_unavailable_count`` (advisor-outage-
          degrade AC-4) are True / >0 when one or more candidates were emitted
          tradeability-unverified by ``plan_tree_compiler.compile_plan`` because
          Composer's backtest endpoint was unreachable — an honest signal distinct
          from a normal gate rejection. Computed over the full candidate list
          BEFORE the Step-2 backtest-success filter, so it stays accurate even
          when that same outage also fails Step 2 and the affected candidates
          never make it into ``candidates`` above.

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

        # Step 1b: Inject caller-provided community candidates (keyword-only, AC-2/AC-3/AC-6).
        # Cap at MAX_COMMUNITY_CANDIDATES_PER_RUN even if the caller passes more — the adapter
        # may have been called with a higher explicit max_candidates.
        # None and [] are both no-ops (AC-6: byte-for-byte identical to the template-only path).
        if community_candidates:
            candidate_infos.extend(community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN])

        # advisor-outage-degrade AC-4: roll up the honest outage signal from the
        # FULL candidate list computed here — NOT from `successful_candidates`
        # below, which Step 2's own per-candidate run_backtest call filters on
        # backtest_error. A real Composer outage fails that Step-2 call too, so
        # a rollup taken after that filter would read 0 in exactly the case
        # this flag exists to surface.
        backtest_unavailable_count = sum(
            1 for info in candidate_infos if info.tradeability_unverified
        )

        if not candidate_infos:
            return ProposalRun(
                candidates=[],
                gated_batch=_empty_gate_batch(),
                screened_survivors=[],
                observations_written=0,
            )

        # Step 2: Backtest each candidate (sequential, 1 req/s via client pacing)
        # One failure never aborts the batch (AC-X5 pattern).

        # Step 2a: Source SPY OOS series ONCE per run via the existing backtest path
        # (AC-25). A 100%-SPY tree is the minimal valid Composer tree for a pure SPY
        # backtest. The same run_backtest client is used for candidates — no new
        # endpoint. On error or empty daily_returns, spy_returns_fn returns {} so the
        # gate's conservative WITHHOLD fires (_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA).
        # The SPY call uses the same symphony_id as candidates so the dvm_capital
        # single-series fallback in _extract_returns works identically.
        _spy_tree = symphony_schema.make_root(
            "SPY Benchmark",
            "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
        )
        _spy_result = run_backtest(_spy_tree, symphony_id=symphony_id)
        if _spy_result.error or not _spy_result.daily_returns:
            # SPY unavailable — conservative WITHHOLD enforced by the gate engine.
            _spy_returns_dict: dict[str, float] = {}
        else:
            # Pct-scale matches dated_returns on candidates (log × 100 → pct).
            _spy_returns_dict = {d: r * 100.0 for d, r in _spy_result.daily_returns.items()}
        _spy_returns_fn = lambda: _spy_returns_dict  # noqa: E731

        bt_candidates: list[BacktestCandidate] = []
        returns_by_id: dict[str, list[float]] = {}

        for info in candidate_infos:
            try:
                result = run_backtest(info.tree, symphony_id=symphony_id)
                if result.error:
                    info.backtest_error = f"backtest failed: {result.error}"
                    info.data_warnings = result.data_warnings
                    continue
                # Convert log returns → pct (EXACTLY as in asset_swap_engine.py:577).
                # Preserve the date keys in a parallel dict for the batch PBO (AC-24)
                # and SPY date-alignment (AC-25). Both use the same ×100 pct scale so
                # the fold transform receives identical values from either field.
                returns_pct = [r * 100.0 for r in result.daily_returns.values()]
                dated_returns_pct = {d: r * 100.0 for d, r in result.daily_returns.items()}
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
                        dated_returns=dated_returns_pct,
                    )
                )
            except Exception as exc:
                info.backtest_error = str(exc)

        # Step 3: FDR gate — full batch, no pre-filtering (AC-3.2).
        # spy_returns_fn carries the SPY OOS series (AC-25); dated_returns on each
        # candidate enables the batch PBO veto (AC-24). Both are wired here; the gate
        # engine handles the SPY-unavailable degradation when spy_returns_fn returns {}.
        gate_batch = evaluate_candidate_batch(
            bt_candidates,
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            spy_returns_fn=_spy_returns_fn,
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
        n_survivors = len(screened_survivors)
        for gate_result in screened_survivors:
            cid = gate_result.candidate_id
            info = next((i for i in candidate_infos if i.candidate_id == cid), None)
            if info is None:
                continue
            returns_pct = returns_by_id.get(cid, [])
            try:
                _persist_survivor(
                    symphony_id,
                    info,
                    gate_result,
                    len(bt_candidates),
                    fdr_q=gate_batch.fdr_q,
                    live_returns=live_returns,
                    returns_pct=returns_pct,
                    n_survivors=n_survivors,
                )
                obs_written += 1
            except Exception:
                logger.warning(
                    "propose_strategies: failed to persist survivor %s",
                    cid,
                    exc_info=True,
                )

        # Step 5b: Persist rejected candidates (gate-rejected or screen-rejected)
        # with verdict=WITHHELD_FDR — [PM-ASSUMED] per phase35 contract.
        screened_survivor_ids = {gr.candidate_id for gr in screened_survivors}
        for gate_result in gate_batch.results:
            cid = gate_result.candidate_id
            if cid in screened_survivor_ids:
                continue  # already persisted as survivor above
            info = next((i for i in candidate_infos if i.candidate_id == cid), None)
            if info is None:
                continue
            returns_pct = returns_by_id.get(cid, [])
            try:
                _persist_rejected(
                    symphony_id,
                    info,
                    gate_result,
                    len(bt_candidates),
                    fdr_q=gate_batch.fdr_q,
                    live_returns=live_returns,
                    returns_pct=returns_pct,
                    n_survivors=n_survivors,
                )
            except Exception:
                logger.warning(
                    "propose_strategies: failed to persist rejected candidate %s",
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
            backtest_unavailable=backtest_unavailable_count > 0,
            backtest_unavailable_count=backtest_unavailable_count,
        )

    except Exception as exc:
        logger.exception("propose_strategies: unexpected error")
        return ProposalRun(
            candidates=[],
            gated_batch=_empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
            error=str(exc),
            error_category=type(exc).__name__,
        )
