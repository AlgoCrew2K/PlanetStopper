"""Retirement Recommender -- Phase 2 Cycle 2a (advisory, read-only math core).

Flags a live symphony as a *retirement candidate* when it is BOTH redundant
(highly correlated with a live sibling) AND the weaker performer of the
correlated pair. Three stages, each conservative and fail-closed:

1. **Screen** (AC-1/AC-2): pairwise Pearson correlation over each symphony's
   CONTINUOUS actual-traded (bot) daily return series -- a thin wrapper over
   ``advisors.correlation_diagnostic.compute_pairwise_correlations``.
2. **Composite rank** (AC-3/AC-4): a CAGR-dominant, fleet-normalized
   performance score identifies which member of a flagged pair is the
   weaker performer (the retirement candidate).
3. **Gates** (AC-5/AC-6): a correlation point estimate alone over-prunes
   crash-diversification (Phase-1 audit finding) -- a recommendation only
   survives if the correlation estimate is statistically robust (uncertainty
   gate) AND the redundancy holds across regimes, not just in calm markets
   (structural-redundancy gate).

This module has NO trade, order, liquidation, deploy, or live-execution
primitive of any kind -- it never moves money and never writes settings.
Every recommendation is persisted purely as an advisory observation row.
Off the 1-minute execution path; never raises (D-1 honest-degradation
contract, matching the sibling advisors modules).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import analytics
import database
from advisors import correlation_diagnostic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (no magic numbers -- every value source-commented)
# ---------------------------------------------------------------------------

# AC-1: pairwise-correlation screen threshold. Plan-pinned literal (the
# Phase-1 audit's "option C" operator ruling) -- a spec value, not tuned.
CORRELATION_SCREEN_THRESHOLD: float = 0.65

# AC-5: minimum overlapping daily observations for a correlation estimate to
# be considered interpretable. Reuses correlation_diagnostic.THIN_DATA_THRESHOLD
# (=30) -- the same Bailey/de Prado (2014) interpretability floor already
# established elsewhere in this codebase, rather than a second independently
# chosen number.
MIN_OBS_FLOOR: int = correlation_diagnostic.THIN_DATA_THRESHOLD

# AC-5: two-sided confidence level for the Fisher z-transform CI on the
# correlation estimate.
UNCERTAINTY_CI_CONFIDENCE: float = 0.95

# The critical value for UNCERTAINTY_CI_CONFIDENCE=0.95. Matches this repo's
# own house convention (tests/guard_preconditions/_reference_stats.py's
# Z_95=1.96), not scipy.stats.norm.ppf(0.975)'s more precise 1.959964.
_Z_95: float = 1.96

# AC-6: the stressed sub-window's correlation must also clear this bar for a
# pair to be considered redundant ACROSS regimes, not just in calm markets.
# Same numeric value as CORRELATION_SCREEN_THRESHOLD (redundancy must hold
# under stress at the same bar it was flagged at under calm conditions) but
# a deliberately separate named constant so the two can be tuned
# independently later without an implicit coupling.
STRESS_REDUNDANCY_THRESHOLD: float = 0.65

# AC-6: minimum aligned observations inside the stressed sub-window for its
# correlation to be considered estimable at all. Independent of, and much
# smaller than, MIN_OBS_FLOOR -- a genuine stress sub-window is a small
# minority of the full history by construction (see STRESS_WINDOW_FRACTION).
STRESS_MIN_OBS: int = 10

# AC-6: fraction of the aligned trading days -- ranked by same-day combined
# return magnitude, descending -- treated as the "stressed" sub-window. 5%
# mirrors the standard 95%-confidence VaR tail convention (the worst/most-
# extreme 5% of days): a genuine stress/crash period is a small minority of
# trading days, not a large fraction. A large, high-magnitude minority is
# mathematically incompatible with the full-window pair remaining a
# CORRELATION_SCREEN_THRESHOLD screen hit at all.
STRESS_WINDOW_FRACTION: float = 0.05

# AC-2 / Architecture: default lookback for the continuous per-symphony bot
# return series feeding both the screen and the composite metrics. 250
# trading days ~= one full trading year -- the same walk-forward window
# length already used elsewhere in this codebase's optimizer.
RETIREMENT_LOOKBACK_DAYS: int = 250

# AC-3: composite weights. CAGR is strictly dominant over each of the other
# four (operator ruling) -- weights sum to 1.0 so the composite stays in the
# same rough numeric range as any one normalized input metric ([0, 1]).
W_CAGR: float = 0.40
W_SHARPE: float = 0.20
W_SORTINO: float = 0.15
W_MAXDD: float = 0.15
W_CALMAR: float = 0.10

# The 5 compute_quantstats_metrics keys the composite consumes -- reused
# verbatim (no renaming/translation layer, per the RED handoff's authoritative
# raw_response schema).
_METRIC_KEYS: tuple[str, ...] = (
    "annualized_return",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
)

_METRIC_WEIGHTS: dict[str, float] = {
    "annualized_return": W_CAGR,
    "sharpe": W_SHARPE,
    "sortino": W_SORTINO,
    "max_drawdown": W_MAXDD,
    "calmar": W_CALMAR,
}

# AC-8: raw_response.basis_label -- must reference the actual-traded/bot
# basis, never claim a held/if-held basis (the AC-2 basis pin, surfaced to
# the operator alongside every recommendation).
_BASIS_LABEL: str = "actual-traded (bot) daily returns"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CompositeScore:
    """Fleet-normalized composite performance score for one symphony.

    composite: the CAGR-dominant weighted composite, higher = better/keep,
        lower = weaker/retirement candidate. None when ineligible.
    metrics: the 5 raw compute_quantstats_metrics values this symphony's
        composite was derived from (same key names, no renaming layer).
    eligible: False iff ANY of the 5 metrics is None -- an ineligible
        symphony is never returned as a retirement candidate (AC-11).
    """

    composite: float | None
    metrics: dict[str, float | None]
    eligible: bool


@dataclass
class GateVerdict:
    """Pass/fail verdict from one of the two AC-5/AC-6 conservative gates.

    ci_lower/ci_upper are populated by evaluate_uncertainty_gate only (feed
    raw_response's ci_lower/ci_upper at persist time); the structural-
    redundancy gate's verdict needs no extra evidence fields of its own --
    stressed_corr/holdings_overlap are already its inputs, threaded straight
    into raw_response by the orchestrator.
    """

    passed: bool
    reason: str | None
    ci_lower: float | None = None
    ci_upper: float | None = None


# ---------------------------------------------------------------------------
# AC-1: screen (thin wrapper over correlation_diagnostic)
# ---------------------------------------------------------------------------


def screen_correlated_pairs(
    series_by_symphony: dict[str, dict[str, float]],
) -> list[correlation_diagnostic.PairResult]:
    """Return every pair whose correlation clears CORRELATION_SCREEN_THRESHOLD.

    A pair whose PairResult.correlation is None (zero-variance / too few
    aligned observations to compute Pearson r at all) is never a screen hit.
    Never raises -- fewer than 2 symphonies yields an empty list (the
    underlying compute_pairwise_correlations' own contract).
    """
    all_pairs = correlation_diagnostic.compute_pairwise_correlations(series_by_symphony)
    return [
        p
        for p in all_pairs
        if p.correlation is not None and p.correlation >= CORRELATION_SCREEN_THRESHOLD
    ]


# ---------------------------------------------------------------------------
# AC-3/AC-4: composite scoring + candidate selection
# ---------------------------------------------------------------------------


def compute_composite_scores(
    metrics_by_symphony: dict[str, dict],
) -> dict[str, CompositeScore]:
    """Fleet-normalized, CAGR-dominant composite score per symphony (AC-3).

    Each of the 5 metrics is min-max normalized across the current fleet's
    ELIGIBLE symphonies only (an ineligible symphony's partial values must
    never distort the range its eligible peers are scored against), then
    combined via the named weights. All 5 raw metrics already use a "higher
    = better" convention (max_drawdown is <= 0, so a shallower/less-negative
    value is already numerically higher) -- no per-metric sign inversion is
    needed; normalizing the raw values directly preserves that ordering.

    A symphony with ANY None among the 5 metrics is ineligible (AC-11) and
    receives composite=None.
    """
    raw: dict[str, dict[str, float | None]] = {}
    eligible: dict[str, bool] = {}
    for sym, metrics in metrics_by_symphony.items():
        vals = {k: metrics.get(k) for k in _METRIC_KEYS}
        raw[sym] = vals
        eligible[sym] = all(v is not None for v in vals.values())

    ranges: dict[str, tuple[float, float]] = {}
    for k in _METRIC_KEYS:
        finite_vals = [raw[sym][k] for sym in raw if eligible[sym]]  # type: ignore[misc]
        ranges[k] = (min(finite_vals), max(finite_vals)) if finite_vals else (0.0, 0.0)

    scores: dict[str, CompositeScore] = {}
    for sym, vals in raw.items():
        if not eligible[sym]:
            scores[sym] = CompositeScore(composite=None, metrics=vals, eligible=False)
            continue
        composite = 0.0
        for k in _METRIC_KEYS:
            lo, hi = ranges[k]
            v = vals[k]
            assert v is not None  # guaranteed by eligible[sym] above
            # Degenerate fleet range (a single eligible symphony, or all
            # eligible symphonies tied on this metric) -- neutral midpoint;
            # no relative ranking information exists.
            normalized = (v - lo) / (hi - lo) if hi > lo else 0.5
            composite += _METRIC_WEIGHTS[k] * normalized
        scores[sym] = CompositeScore(composite=composite, metrics=vals, eligible=True)
    return scores


def select_retirement_candidate(
    sym_a: str,
    sym_b: str,
    scores: dict[str, CompositeScore],
) -> str | None:
    """Return the retirement CANDIDATE's symphony_id for a flagged pair, or
    None when no valid candidate exists (AC-4).

    The candidate is the LOWER-composite member. Ties broken by: (a) lower
    composite; (b) tie -> lower metrics['annualized_return']; (c) tie ->
    lexically smaller symphony_id. Symmetric in (sym_a, sym_b) -- the result
    identifies an entity, not a call-order-dependent position.

    An ineligible symphony (CompositeScore.eligible is False, composite is
    None) is NEVER returned as the candidate. PM ruling (PR-level
    /code-review Finding 1): if EITHER side of the pair is ineligible, the
    pair yields NO candidate at all (fail-closed) -- never a fallback
    nominating the eligible sibling, regardless of which side (natural
    candidate or natural keep member) is the ineligible one. A symphony
    missing from `scores` entirely is treated the same as "no valid pair"
    (never a KeyError).
    """
    score_a = scores.get(sym_a)
    score_b = scores.get(sym_b)
    if score_a is None or score_b is None:
        return None
    if not score_a.eligible and not score_b.eligible:
        return None

    comp_a, comp_b = score_a.composite, score_b.composite
    # PM ruling (PR-level /code-review Finding 1) OVERRIDES the original
    # AC-11-derived "may still be the keep member" design: when EITHER side
    # is ineligible (composite is None), the pair yields NO candidate at all
    # -- never a fallback nominating the eligible sibling. composite=None can
    # hide a catastrophic/unmeasurable loss; keeping the unscoreable member
    # while retiring the well-characterized one is backwards for a capital
    # decision. (Was `comp_a is None and comp_b is None` -- fail-open on
    # exactly one side ineligible.)
    if comp_a is None or comp_b is None:
        return None
    if comp_a < comp_b:
        natural = sym_a
    elif comp_b < comp_a:
        natural = sym_b
    else:
        cagr_a = score_a.metrics.get("annualized_return")
        cagr_b = score_b.metrics.get("annualized_return")
        if cagr_a is not None and cagr_b is not None and cagr_a != cagr_b:
            natural = sym_a if cagr_a < cagr_b else sym_b
        else:
            natural = sym_a if sym_a < sym_b else sym_b

    return natural if scores[natural].eligible else None


# ---------------------------------------------------------------------------
# AC-5: uncertainty gate
# ---------------------------------------------------------------------------


def evaluate_uncertainty_gate(pair: correlation_diagnostic.PairResult) -> GateVerdict:
    """Passes iff the Fisher-z 95% CI LOWER BOUND on the pair's correlation is
    also >= CORRELATION_SCREEN_THRESHOLD AND n_obs >= MIN_OBS_FLOOR (AC-5).

    Fails closed -- never raises -- when correlation is None, n_obs is below
    the floor, or the Fisher z formula is otherwise undefined (n<=3 or
    |r|>=1.0; MIN_OBS_FLOOR > 3 makes the n<=3 case unreachable once the
    floor check passes, but it is guarded explicitly regardless).
    """
    if pair.correlation is None:
        return GateVerdict(passed=False, reason="correlation is undefined")
    if pair.n_obs < MIN_OBS_FLOOR:
        return GateVerdict(
            passed=False,
            reason=f"n_obs {pair.n_obs} < MIN_OBS_FLOOR {MIN_OBS_FLOOR}",
        )

    r, n = pair.correlation, pair.n_obs
    if n <= 3 or not (-1.0 < r < 1.0):
        return GateVerdict(passed=False, reason="Fisher z-transform undefined for this input")

    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    ci_lower = math.tanh(z - _Z_95 * se)
    ci_upper = math.tanh(z + _Z_95 * se)

    passed = ci_lower >= CORRELATION_SCREEN_THRESHOLD
    reason = None if passed else f"CI lower bound {ci_lower:.4f} < {CORRELATION_SCREEN_THRESHOLD}"
    return GateVerdict(passed=passed, reason=reason, ci_lower=ci_lower, ci_upper=ci_upper)


# ---------------------------------------------------------------------------
# AC-6: structural-redundancy gate
# ---------------------------------------------------------------------------


def evaluate_structural_redundancy_gate(
    pair: correlation_diagnostic.PairResult,
    stressed_corr: float | None,
    holdings_overlap: float | None,
) -> GateVerdict:
    """Passes iff stressed_corr is not None AND
    stressed_corr >= STRESS_REDUNDANCY_THRESHOLD (AC-6).

    stressed_corr=None is the orchestrator's single signal for BOTH "the
    stress sub-window has fewer than STRESS_MIN_OBS aligned days" and "the
    stress-window Pearson r is itself undefined" -- mirroring
    PairResult.correlation's own None convention (one signal, two causes,
    both fail-closed identically).

    holdings_overlap is CORROBORATING evidence only -- recorded by the
    orchestrator into raw_response, but never a blocking or rescuing input
    to this gate by itself; the parameter exists for a symmetric call
    signature and possible future use, not consumed in the pass/fail logic.
    """
    del holdings_overlap  # corroborating only -- never blocks/rescues this gate
    if stressed_corr is None:
        return GateVerdict(passed=False, reason="stressed-window correlation undefined or too thin")
    passed = stressed_corr >= STRESS_REDUNDANCY_THRESHOLD
    reason = (
        None
        if passed
        else f"stressed correlation {stressed_corr:.4f} < {STRESS_REDUNDANCY_THRESHOLD}"
    )
    return GateVerdict(passed=passed, reason=reason)


def _compute_stressed_correlation(vals_a: list[float], vals_b: list[float]) -> float | None:
    """AC-6 stress sub-window: the top ceil(STRESS_WINDOW_FRACTION * n)
    aligned days ranked by MOST-NEGATIVE combined return
    ((return_a[i] + return_b[i]) / 2) ascending -- the deepest joint-drawdown
    subset of the aligned history, targeting genuine crash/stress days.

    PM ruling (PR-level /code-review Finding 3): NOT magnitude-based
    (max(|return_a|, |return_b|) descending) -- magnitude selection admits
    big RALLY (up) days alongside genuine crash (down) days, so a pair that
    co-moves nicely on rallies but DIVERGES on drawdowns (the exact crash-
    diversification case this gate exists to protect, per the Phase-1 audit)
    can have its "stressed" window filled entirely with well-correlated
    rally days, never sampling the divergent crash days at all -- silently
    passing the gate for a pair that should have been withheld. Downside/
    most-negative selection targets the crash days specifically.

    Returns None (fail-closed, mirroring PairResult.correlation's own
    convention) when the selected subset is too thin (< STRESS_MIN_OBS) or
    the Pearson r over it is itself undefined (zero variance).
    """
    n = len(vals_a)
    k = math.ceil(STRESS_WINDOW_FRACTION * n)
    if k < STRESS_MIN_OBS:
        return None

    combined = [(vals_a[i] + vals_b[i]) / 2 for i in range(n)]
    top_idx = sorted(range(n), key=lambda i: combined[i])[:k]  # ascending -- most negative first
    stress_a = [vals_a[i] for i in top_idx]
    stress_b = [vals_b[i] for i in top_idx]

    # Reuse correlation_diagnostic's own Pearson-r-or-None implementation
    # (module-qualified, deliberately the private helper per the RED
    # handoff's architectural ruling) so the stress-window statistic shares
    # the EXACT SAME formula/None-convention as the full-window screen --
    # never a second, independently-drifting correlation implementation.
    return correlation_diagnostic._pearson_r(stress_a, stress_b)  # noqa: SLF001


def _compute_holdings_overlap(
    holdings_a: dict,
    holdings_b: dict,
) -> float | None:
    """Jaccard overlap (|intersection| / |union|) of two symphonies' held
    ticker sets -- corroborating evidence for the structural-redundancy gate
    (AC-6). None when either side's holdings are unavailable (off-hours /
    flat market, when logic_holdings is empty) -- must never crash or be
    treated as zero overlap, which would be a fabricated signal.
    """
    if not holdings_a or not holdings_b:
        return None
    tickers_a, tickers_b = set(holdings_a), set(holdings_b)
    union = tickers_a | tickers_b
    if not union:
        return None
    return len(tickers_a & tickers_b) / len(union)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _live_symphony_roster(bot_state: dict) -> list[str]:
    """Structural discriminator mirroring the house convention (isinstance(v,
    dict) and 'name' in v) for distinguishing a real symphony entry from
    bot_state's top-level portfolio metadata keys (date, last_execution_mode,
    etc.) -- every real symphony entry carries a "name" key."""
    return sorted(
        sym for sym, entry in bot_state.items() if isinstance(entry, dict) and "name" in entry
    )


def _evidence_strength(raw_response: dict) -> float:
    """Ranks competing recommendations for the same candidate when a
    correlation cluster (the same symphony flagged against multiple
    siblings) produces more than one otherwise-independent hit. Higher raw
    full-window correlation = the stronger, more clear-cut evidence of
    redundancy -- used only to pick ONE surviving recommendation per
    candidate at build time (dedup)."""
    return raw_response["correlation"]


def build_recommendations(
    *,
    db_file: str | None = None,
    days: int | None = RETIREMENT_LOOKBACK_DAYS,
) -> list[dict]:
    """Orchestrator: discover the live roster, screen, score, gate, and
    assemble the raw_response evidence dict for every surviving
    recommendation (AC-1..AC-11). Never raises -- any unexpected failure
    degrades to an empty, honest result.

    Returns a list of flat dicts, each shaped exactly like the raw_response
    schema documented in the module docstring / persist_recommendations.
    """
    try:
        bot_state = database.load_state()
        symphony_ids = _live_symphony_roster(bot_state)
        if len(symphony_ids) < 2:
            return []

        series_by_symphony: dict[str, dict[str, float]] = {}
        holdings_by_symphony: dict[str, dict] = {}
        for sym in symphony_ids:
            result = analytics.get_symphony_bot_and_held_daily_returns(
                sym, db_file=db_file, days=days
            )
            if result is None:
                continue
            dates, bot_returns, _held_returns = result  # AC-2: bot ([1]), never held ([2])
            series_by_symphony[sym] = dict(zip(dates, bot_returns, strict=True))
            holdings_by_symphony[sym] = bot_state[sym].get("logic_holdings") or {}

        if len(series_by_symphony) < 2:
            return []

        pairs = screen_correlated_pairs(series_by_symphony)
        if not pairs:
            return []

        # AC-3: "normalized across the current fleet" -- the FULL live roster
        # with a usable return series (series_by_symphony's keys), never just
        # the narrower subset of symphonies that happen to appear in a
        # flagged pair. A pair-only population is not a fleet at all, and is
        # mathematically degenerate for min-max normalization (every
        # non-tied metric collapses to a winner-take-all {0, 1}, discarding
        # all magnitude information) -- confirmed to flip which symphony is
        # selected as the retirement candidate purely as a function of
        # whether an unrelated third live symphony exists in the roster
        # (quant-code-reviewer Finding 1).
        metrics_by_symphony = {
            sym: analytics.compute_quantstats_metrics(list(series_by_symphony[sym].values()))
            for sym in series_by_symphony
        }
        scores = compute_composite_scores(metrics_by_symphony)

        candidates_by_id: dict[str, dict] = {}
        for pair in pairs:
            uncertainty_verdict = evaluate_uncertainty_gate(pair)
            if not uncertainty_verdict.passed:
                continue

            vals_a, vals_b, _window = correlation_diagnostic._extract_aligned_pairs(  # noqa: SLF001
                series_by_symphony[pair.sym_a], series_by_symphony[pair.sym_b]
            )
            stressed_corr = _compute_stressed_correlation(vals_a, vals_b)
            holdings_overlap = _compute_holdings_overlap(
                holdings_by_symphony.get(pair.sym_a) or {},
                holdings_by_symphony.get(pair.sym_b) or {},
            )

            redundancy_verdict = evaluate_structural_redundancy_gate(
                pair, stressed_corr=stressed_corr, holdings_overlap=holdings_overlap
            )
            if not redundancy_verdict.passed:
                continue

            candidate_id = select_retirement_candidate(pair.sym_a, pair.sym_b, scores)
            if candidate_id is None:
                continue
            sibling_id = pair.sym_b if candidate_id == pair.sym_a else pair.sym_a

            raw_response = {
                "candidate_id": candidate_id,
                "sibling_id": sibling_id,
                "correlation": pair.correlation,
                "ci_lower": uncertainty_verdict.ci_lower,
                "ci_upper": uncertainty_verdict.ci_upper,
                "n_obs": pair.n_obs,
                "candidate_composite": scores[candidate_id].composite,
                "sibling_composite": scores[sibling_id].composite,
                "candidate_metrics": dict(scores[candidate_id].metrics),
                "sibling_metrics": dict(scores[sibling_id].metrics),
                "uncertainty_gate_passed": uncertainty_verdict.passed,
                "structural_redundancy_gate_passed": redundancy_verdict.passed,
                "stressed_correlation": stressed_corr,
                "holdings_overlap": holdings_overlap,
                "basis_label": _BASIS_LABEL,
            }

            existing = candidates_by_id.get(candidate_id)
            if existing is None or _evidence_strength(raw_response) > _evidence_strength(existing):
                candidates_by_id[candidate_id] = raw_response

        return [candidates_by_id[k] for k in sorted(candidates_by_id)]
    except Exception as exc:
        # D-1 contract: log only type(exc).__name__, never str(exc) -- an
        # exception message could carry a file path, DB row content, or
        # other internal detail that shouldn't reach logs at WARNING+. This
        # still lets the nightly scheduler tick distinguish a genuine
        # internal crash from an honest "no recommendations tonight" empty
        # result (PR-level /code-review Finding 5).
        logger.warning("build_recommendations: internal failure: %s", type(exc).__name__)
        return []


def persist_recommendations(recs: list[dict], *, db_file: str | None = None) -> int:
    """Persist each recommendation as one append-only advisor_observations
    row (AC-8). Returns the count persisted.

    db_file is accepted for interface symmetry with build_recommendations --
    database.insert_advisor_observation has no db_file override of its own
    (it always writes through database.DB_FILE), so this parameter is not
    threaded further; it lets callers pass the same db_file used for
    build_recommendations without the two calls looking asymmetric.
    """
    del db_file  # see docstring -- no override exists on the write path
    count = 0
    for rec in recs:
        database.insert_advisor_observation(
            advisor_role="RETIREMENT_RECOMMENDATION",
            subject_type="symphony",
            subject_id=rec["candidate_id"],
            symphony_id=rec["candidate_id"],
            verdict="retire_candidate",
            raw_response=rec,
        )
        count += 1
    return count
