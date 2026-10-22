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

Two return-series sources feed the same pipeline (Cycle 2e): ``"shadow"`` (the
default -- each symphony's continuous actual-traded series from shadow_history)
and ``"composer"`` (each symphony's realized, deposit-adjusted daily returns from
Composer's account-scoped history, fetched read-only through
``advisors.composer_series_client``). One basis per run -- the Composer source
never backfills from shadow_history. ``build_recommendations_with_summary``
additionally returns an honest per-run summary (counts per gate outcome,
exclusion reasons) so a run with zero recommendations is still explainable.

This module has NO trade, order, liquidation, deploy, or live-execution
primitive of any kind -- it never moves money and never writes settings.
Every recommendation is persisted purely as an advisory observation row.
Off the 1-minute execution path; never raises (D-1 honest-degradation
contract, matching the sibling advisors modules).
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

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

# Cycle 2e: the two selectable return-series sources.
SOURCE_SHADOW: str = "shadow"
SOURCE_COMPOSER: str = "composer"

# Cycle 2e AC-2: basis label for recommendations built from Composer's realized
# deposit-adjusted daily series. Exact literal pinned by the operator-facing
# basis disclosure -- never claims the actual-traded (bot) basis.
_COMPOSER_BASIS_LABEL: str = "realized daily returns (Composer, deposit-adjusted)"

# Cycle 2e AC-2: minimum spacing (seconds) between consecutive Composer fetches.
# Composer's documented rate limit is 1 request/second; one fetch per symphony is
# issued back-to-back, so each fetch after the first waits this long first.
COMPOSER_FETCH_MIN_INTERVAL_SECONDS: float = 1.0

# Upper bound for the STRESS_GATE_MIN_ALIGNED_DAYS derivation below -- a sanity
# ceiling (about 3,900 trading years of daily data), never a tuned value.
_STRESS_GATE_SEARCH_LIMIT: int = 1_000_000


# Cycle 2e AC-3: the smallest aligned-day count at which the stress sub-window can
# be estimable at all -- the smallest n with ceil(STRESS_WINDOW_FRACTION * n) >=
# STRESS_MIN_OBS (the exact test _compute_stressed_correlation applies). DERIVED
# from the two stress constants, never a second hand-typed literal, so retuning
# either constant can never leave this disclosed minimum stale. Surfaced in the
# run summary so an operator seeing "blocked: stress window too thin" knows the
# aligned-history length at which that gate can first pass.
def _derive_stress_gate_min_aligned_days() -> int:
    """Smallest n with ceil(STRESS_WINDOW_FRACTION * n) >= STRESS_MIN_OBS.

    The search is bounded: a nonsensical stress constant (e.g. a zero fraction,
    for which no n ever qualifies) must fail LOUDLY at import instead of hanging
    application start-up in an unbounded loop.
    """
    for n in range(1, _STRESS_GATE_SEARCH_LIMIT + 1):
        if math.ceil(STRESS_WINDOW_FRACTION * n) >= STRESS_MIN_OBS:
            return n
    raise ValueError(
        "STRESS_WINDOW_FRACTION/STRESS_MIN_OBS admit no aligned-day count within "
        f"{_STRESS_GATE_SEARCH_LIMIT} days"
    )


STRESS_GATE_MIN_ALIGNED_DAYS: int = _derive_stress_gate_min_aligned_days()

# Cycle 2e AC-3: run-summary status values.
STATUS_OK: str = "ok"
STATUS_INSUFFICIENT_SYMPHONIES: str = "insufficient_symphonies"
STATUS_SERIES_UNAVAILABLE: str = "series_unavailable"
STATUS_INTERNAL_ERROR: str = "internal_error"

# Cycle 2e AC-2/AC-3: per-symphony exclusion reasons (exact strings are pinned).
_REASON_NO_ACCOUNT: str = "no_account"
_REASON_SERIES_TOO_SHORT: str = "series_too_short"
_REASON_FETCH_ERROR_PREFIX: str = "fetch_error: "
_REASON_SHADOW_UNAVAILABLE: str = "shadow_series_unavailable"

# AC-2 / AC-11: a symphony needs at least this many daily returns to be kept
# (a single return has no variance, so no correlation or CAGR can be derived).
_MIN_RETURNS_TO_KEEP: int = 2

# Run-timestamp format (UTC, tz-aware ISO-8601 with a literal Z suffix).
_RUN_AT_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"


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


def _new_run_summary(*, source: str, days: int | None) -> dict:
    """The full run-summary envelope with every key present (Cycle 2e AC-3).

    Counters start at their zero/None "nothing happened" values and are filled in
    as the pipeline progresses, so even an early exit or an internal failure still
    returns a complete, honest envelope.
    """
    return {
        "run_id": uuid.uuid4().hex,
        "run_at": datetime.now(UTC).strftime(_RUN_AT_FORMAT),
        "source": source,
        "basis_label": _COMPOSER_BASIS_LABEL if source == SOURCE_COMPOSER else _BASIS_LABEL,
        "status": STATUS_OK,
        "error_type": None,
        "roster_size": 0,
        "symphonies_with_series": 0,
        "symphonies_without_series": [],
        "series_days_min": None,
        "series_days_max": None,
        "lookback_days": days,
        "pairs_evaluated": 0,
        "pairs_screened": 0,
        "blocked_uncertainty": 0,
        "blocked_stress_too_thin": 0,
        "blocked_stress_below_threshold": 0,
        "blocked_no_candidate": 0,
        "recommendations": 0,
        "screen_threshold": CORRELATION_SCREEN_THRESHOLD,
        "stress_gate_min_aligned_days": STRESS_GATE_MIN_ALIGNED_DAYS,
    }


def _collect_shadow_series(
    symphony_ids: list[str],
    bot_state: dict,
    *,
    db_file: str | None,
    days: int | None,
    excluded: list[dict],
) -> tuple[dict[str, dict[str, float]], dict[str, dict]]:
    """Per-symphony continuous actual-traded (bot) series from shadow_history."""
    series_by_symphony: dict[str, dict[str, float]] = {}
    holdings_by_symphony: dict[str, dict] = {}
    for sym in symphony_ids:
        result = analytics.get_symphony_bot_and_held_daily_returns(sym, db_file=db_file, days=days)
        if result is None:
            excluded.append({"symphony_id": sym, "reason": _REASON_SHADOW_UNAVAILABLE})
            continue
        dates, bot_returns, _held_returns = result  # AC-2: bot ([1]), never held ([2])
        series_by_symphony[sym] = dict(zip(dates, bot_returns, strict=True))
        holdings_by_symphony[sym] = bot_state[sym].get("logic_holdings") or {}
    return series_by_symphony, holdings_by_symphony


def _collect_composer_series(
    symphony_ids: list[str],
    bot_state: dict,
    *,
    days: int | None,
    excluded: list[dict],
) -> tuple[dict[str, dict[str, float]], dict[str, dict]]:
    """Per-symphony realized daily-return series from Composer (Cycle 2e AC-2).

    One basis per run: a symphony without an account id, or whose fetch fails, is
    EXCLUDED with a reason -- it is never backfilled from shadow_history. Fetches
    are spaced COMPOSER_FETCH_MIN_INTERVAL_SECONDS apart (Composer's 1 req/s limit).
    """
    # CC-2 lazy import, resolved as a module attribute at CALL time so the shadow
    # source never loads the Composer client and tests can patch the seam.
    from advisors import composer_series_client  # noqa: PLC0415

    series_by_symphony: dict[str, dict[str, float]] = {}
    holdings_by_symphony: dict[str, dict] = {}
    fetched_any = False
    for sym in symphony_ids:
        account = bot_state[sym].get("account")
        if not account:
            excluded.append({"symphony_id": sym, "reason": _REASON_NO_ACCOUNT})
            continue

        if fetched_any:
            time.sleep(COMPOSER_FETCH_MIN_INTERVAL_SECONDS)
        fetched_any = True
        result = composer_series_client.fetch_symphony_daily_returns(account, sym)
        if result.error:
            excluded.append(
                {"symphony_id": sym, "reason": f"{_REASON_FETCH_ERROR_PREFIX}{result.error}"}
            )
            continue

        ordered = sorted(result.returns.items())
        if days is not None:
            ordered = ordered[-days:]  # most recent `days` returns
        if len(ordered) < _MIN_RETURNS_TO_KEEP:
            excluded.append({"symphony_id": sym, "reason": _REASON_SERIES_TOO_SHORT})
            continue
        series_by_symphony[sym] = dict(ordered)
        holdings_by_symphony[sym] = bot_state[sym].get("logic_holdings") or {}
    return series_by_symphony, holdings_by_symphony


def _run_pipeline(
    summary: dict, *, db_file: str | None, days: int | None, source: str
) -> list[dict]:
    """Discover roster -> collect series -> screen -> score -> gate -> recommend.

    Mutates ``summary`` in place with the counts it observes. May raise; the
    caller (build_recommendations_with_summary) owns the D-1 never-raise guard.
    """
    if source not in (SOURCE_SHADOW, SOURCE_COMPOSER):
        raise ValueError("unknown return-series source")

    bot_state = database.load_state()
    symphony_ids = _live_symphony_roster(bot_state)
    summary["roster_size"] = len(symphony_ids)
    if len(symphony_ids) < 2:
        summary["status"] = STATUS_INSUFFICIENT_SYMPHONIES
        return []

    excluded: list[dict] = summary["symphonies_without_series"]
    if source == SOURCE_COMPOSER:
        series_by_symphony, holdings_by_symphony = _collect_composer_series(
            symphony_ids, bot_state, days=days, excluded=excluded
        )
    else:
        series_by_symphony, holdings_by_symphony = _collect_shadow_series(
            symphony_ids, bot_state, db_file=db_file, days=days, excluded=excluded
        )

    summary["symphonies_with_series"] = len(series_by_symphony)
    if series_by_symphony:
        lengths = [len(series) for series in series_by_symphony.values()]
        summary["series_days_min"] = min(lengths)
        summary["series_days_max"] = max(lengths)
    if len(series_by_symphony) < 2:
        summary["status"] = STATUS_SERIES_UNAVAILABLE
        return []

    summary["pairs_evaluated"] = len(
        correlation_diagnostic.compute_pairwise_correlations(series_by_symphony)
    )
    pairs = screen_correlated_pairs(series_by_symphony)
    summary["pairs_screened"] = len(pairs)
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
    basis_label = summary["basis_label"]

    candidates_by_id: dict[str, dict] = {}
    for pair in pairs:
        uncertainty_verdict = evaluate_uncertainty_gate(pair)
        if not uncertainty_verdict.passed:
            summary["blocked_uncertainty"] += 1
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
            # stressed_corr is None = window too thin / undefined; otherwise the
            # stressed correlation was estimable but below the redundancy bar.
            if stressed_corr is None:
                summary["blocked_stress_too_thin"] += 1
            else:
                summary["blocked_stress_below_threshold"] += 1
            continue

        candidate_id = select_retirement_candidate(pair.sym_a, pair.sym_b, scores)
        if candidate_id is None:
            summary["blocked_no_candidate"] += 1
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
            "basis_label": basis_label,
        }

        existing = candidates_by_id.get(candidate_id)
        if existing is None or _evidence_strength(raw_response) > _evidence_strength(existing):
            candidates_by_id[candidate_id] = raw_response

    return [candidates_by_id[k] for k in sorted(candidates_by_id)]


def build_recommendations_with_summary(
    *,
    db_file: str | None = None,
    days: int | None = RETIREMENT_LOOKBACK_DAYS,
    source: str = SOURCE_SHADOW,
) -> tuple[list[dict], dict]:
    """Orchestrator: discover the live roster, screen, score, gate, and
    assemble the raw_response evidence dict for every surviving
    recommendation (AC-1..AC-11), together with an honest run summary
    (Cycle 2e AC-3). Never raises -- any unexpected failure degrades to an
    empty recommendation list and a summary with status "internal_error".

    ``source`` selects the return-series basis: "shadow" (default, shadow_history
    actual-traded series) or "composer" (Composer realized deposit-adjusted daily
    returns). ``days`` is the lookback: the most recent ``days`` returns per
    symphony (None = all available).

    Returns ``(recommendations, summary)``. Each recommendation is a flat dict
    shaped exactly like the raw_response schema documented in
    persist_recommendations. The summary carries every documented key on every
    status; its per-gate counts are per PAIR, so
    ``pairs_screened == sum(blocked_*) + pairs that produced a recommendation``
    (per-candidate dedup can then collapse several produced pairs into fewer
    recommendations).
    """
    summary = _new_run_summary(source=source, days=days)
    try:
        recs = _run_pipeline(summary, db_file=db_file, days=days, source=source)
    except Exception as exc:
        # D-1 contract: log only type(exc).__name__, never str(exc) -- an
        # exception message could carry a file path, DB row content, or
        # other internal detail that shouldn't reach logs at WARNING+. This
        # still lets the nightly scheduler tick distinguish a genuine
        # internal crash from an honest "no recommendations tonight" empty
        # result (PR-level /code-review Finding 5).
        logger.warning("build_recommendations: internal failure: %s", type(exc).__name__)
        summary["status"] = STATUS_INTERNAL_ERROR
        summary["error_type"] = type(exc).__name__
        summary["recommendations"] = 0
        return [], summary
    summary["recommendations"] = len(recs)
    return recs, summary


def build_recommendations(
    *,
    db_file: str | None = None,
    days: int | None = RETIREMENT_LOOKBACK_DAYS,
    source: str = SOURCE_SHADOW,
) -> list[dict]:
    """The recommendations half of build_recommendations_with_summary.

    Same keyword arguments, same never-raises contract; returns exactly
    ``build_recommendations_with_summary(...)[0]``.
    """
    return build_recommendations_with_summary(db_file=db_file, days=days, source=source)[0]


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
