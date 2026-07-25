"""Strategy Incubation Gate — forward paper-tracking ledger for Strategy Builder survivors.

Strategy Builder survivors (built-new + atlas-suggested candidates that pass the
FDR/PBO/SPY gates) no longer surface as recommendations immediately. Each survivor
enters this forward-incubation ledger and is tracked on real forward market data
(paper, zero capital, zero execution) for a minimum window; only candidates meeting
forward criteria are PROMOTED to recommendation status.

Why (AC-8): forward data collected after admission post-dates the generating LLM's
training cutoff by construction — the first memorization-free signal anywhere in
this pipeline. This is why a promotion decision is never made from the backtest
series alone; see the citation at the decision site in evaluate_promotion() below,
and DECISIONS.md.

Advisory-only end to end; zero live-execution surface; zero change to the 1-minute
engine decision path (AC-9, enforced by
tests/advisors/test_incubation_blast_radius.py). Off-execution-path, CC-2
lazy-imported from app.py's daemon thread, D-1 never-raises throughout.

Module placement / layering (.claude/tdd-handoff.md "Module placement"):
database.py is a lower layer that this module imports FROM — it must never
import FROM advisors.*. MAX_INCUBATING / INCUBATION_REFRACTORY_DAYS therefore
live in database.py next to the accessor that enforces them; the three
constants below are the ones owned by tick/promotion logic here.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import database
import market_calendar
from advisors import community_strats, symphony_schema
from advisors.composer_backtest_client import run_backtest
from advisors.plan_tree_compiler import _parse_envelope_status
from analytics import compute_quantstats_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (operator knobs — NEVER Optuna/advisor-tunable; house
# no-magic-numbers rule, same convention as math_engine.py).
# ---------------------------------------------------------------------------

# Minimum forward trading days observed before promotion is possible (~3 months).
INCUBATION_WINDOW_TRADING_DAYS: int = 63

# Forward MDD (positive pct magnitude) must stay <= this multiplier x the
# candidate's stored backtest MDD (also positive pct magnitude) before an
# early fail fires. See "Promotion evaluation" in .claude/tdd-handoff.md.
INCUBATION_MDD_BREACH_MULT: float = 1.5

# Consecutive tick fetch failures for one candidate before EXPIRED.
INCUBATION_MAX_FETCH_FAILURES: int = 5

# Minimum SPY-paired (non-null spy_return_pct) observations required before a
# window-complete evaluation is trusted — below this, promotion DEFERS rather
# than judging benchmark-blind (mirrors backtest_gate_engine's
# _PBO_MIN_ALIGNED_DATES-style guard). Pinned by the golden fixture's
# "defers_when_benchmark_missing" scenario (1 paired observation must defer).
_MIN_SPY_PAIRED_OBSERVATIONS: int = 2

# Same 100%-SPY tree construction as strategy_builder_engine.py:993-998 — the
# tick's shared benchmark call reuses this exact name/shape so a test router
# can dispatch on it without a second convention to maintain.
_SPY_TREE_NAME = "SPY Benchmark"

# Floating-point tolerance on the MDD-breach boundary comparison. quantstats'
# max_drawdown is computed via pandas/numpy float64 arithmetic, which can
# land a fraction of a ULP away from an exact decimal boundary (e.g. a
# -15.0%-exactly scenario measured as -0.15000000000000002 -- verified
# empirically, ~1.8e-15 off). Without this tolerance a mathematically-exact
# non-breach can spuriously trip the strict '>' comparison. 1e-9 is many
# orders of magnitude larger than float64 noise yet far smaller than any
# pct-magnitude difference that could plausibly matter -- same value and same
# rationale as guard_preconditions.py's _BOUNDARY_EPS (absorbs float64
# subtraction noise at exact-by-construction boundaries without blurring any
# intentional distinction; this feature has no adversarial boundary case
# tighter than whole percentage points, several orders of magnitude above
# 1e-9). This epsilon only ever forgives sub-ULP float noise ON THE NON-BREACH
# side of the comparison -- it can never mask a genuine breach, since any
# real-world MDD difference worth caring about is many orders of magnitude
# larger than 1e-9 percentage points.
_MDD_BREACH_EPSILON: float = 1e-9


# ---------------------------------------------------------------------------
# candidate_hash — MUST match community_strats' convention exactly (reuse,
# never fork; a property test pins byte-equality against
# community_strats._composition_hash directly).
# ---------------------------------------------------------------------------


def candidate_hash(tree: dict) -> str:
    """Tree-structural SHA-256 hash, delegating to community_strats' own
    convention (never a forked second implementation)."""
    return community_strats._composition_hash(tree)


# ---------------------------------------------------------------------------
# Admission adapter
# ---------------------------------------------------------------------------


def admit_candidate(
    candidate_hash: str,
    tree_json: str,
    objective: str,
    provenance: str,
    backtest_mdd_pct: float,
) -> dict:
    """Engine-layer entry point for admitting one candidate into the ledger.

    The idempotency/refractory/cap rules are enforced inside
    database.register_incubation_candidate (state-DB write path; see
    .claude/tdd-handoff.md "Idempotency + refractory rule"). This function is
    a thin, stably-named pass-through so the Step-5 wiring in
    strategy_builder_engine.py has a fixed advisors-layer call surface.

    Cap-aware batch ordering (sort the survivor batch by gate-time oos_alpha
    descending, then call this in that order so cap exhaustion naturally
    excludes the lowest-oos_alpha candidates first) is the CALLER's
    responsibility — this function only enforces the cap for whichever single
    call it receives.

    Returns {"admitted": bool, "status": str | None, "reason": str} — the
    same shape database.register_incubation_candidate returns.
    """
    return database.register_incubation_candidate(
        candidate_hash=candidate_hash,
        tree_json=tree_json,
        objective=objective,
        provenance=provenance,
        backtest_mdd_pct=backtest_mdd_pct,
    )


# ---------------------------------------------------------------------------
# Promotion evaluation — PURE function, no DB, no network (structurally
# enforced by tests/advisors/test_incubation_promotion.py's import guard).
# ---------------------------------------------------------------------------


def _compound(returns_pct: list[float]) -> float:
    """Simple-return compounding over a pct-scale series: prod(1+r/100)-1.

    The shipped convention throughout this codebase — never log-return math.
    See composer_backtest_client._extract_returns's own docstring.
    """
    result = 1.0
    for r in returns_pct:
        result *= 1.0 + r / 100.0
    return result - 1.0


def evaluate_promotion(
    forward_return_pct: list[float],
    spy_return_pct: list[float | None],
    backtest_mdd_pct: float,
    days_observed: int,
) -> dict:
    """Pure promotion-evaluation math (AC-4). No I/O of any kind.

    spy_return_pct is date-index-paired with forward_return_pct (same
    length); a None entry marks a day the shared SPY call didn't cover that
    tick.

    Returns {"decision": "PROMOTED"|"FAILED"|"DEFERRED"|"CONTINUE",
             "reason": str | None} — reason is set only for FAILED.
    """
    # --- Early-fail: forward MDD breach fires at ANY day count, before the
    # window completes (AC-4) — checked unconditionally, first. A window-
    # complete PROMOTED decision below is therefore only ever reached once
    # this early-fail branch has already ruled out a breach for this tick,
    # which is exactly the joint "alpha >= 0 AND MDD within multiplier"
    # condition the handoff specifies for the window-complete case.
    forward_metrics = compute_quantstats_metrics(forward_return_pct)
    forward_mdd_fraction = forward_metrics.get("max_drawdown")
    if forward_mdd_fraction is not None:
        # quantstats convention: max_drawdown is a NEGATIVE fraction. Convert
        # to a positive pct magnitude before comparing against
        # backtest_mdd_pct, which is stored the same way (see the migration
        # section of .claude/tdd-handoff.md — analytics.py:441-443).
        forward_mdd_pct_magnitude = abs(forward_mdd_fraction) * 100.0
        if (
            forward_mdd_pct_magnitude
            > INCUBATION_MDD_BREACH_MULT * backtest_mdd_pct + _MDD_BREACH_EPSILON
        ):
            return {"decision": "FAILED", "reason": "mdd_breach"}
    # forward_mdd_fraction is None ("insufficient data" per
    # compute_quantstats_metrics) is treated as "cannot evaluate MDD this
    # tick" — never coerced to 0, which would trivially avoid ever breaching.

    # --- Window not yet complete and no early-fail this tick: still
    # incubating, no decision.
    if days_observed < INCUBATION_WINDOW_TRADING_DAYS:
        return {"decision": "CONTINUE", "reason": None}

    # --- Benchmark-missing guard: date-paired exclusion of null SPY days.
    # A missing SPY day is EXCLUDED from both sides' compounding for that
    # date, never silently treated as a 0% day (mirrors
    # backtest_gate_engine's SPY alignment).
    paired_forward = [
        f for f, s in zip(forward_return_pct, spy_return_pct, strict=True) if s is not None
    ]
    paired_spy = [s for s in spy_return_pct if s is not None]
    if len(paired_spy) < _MIN_SPY_PAIRED_OBSERVATIONS:
        # Window complete but too few SPY-paired observations to judge —
        # DEFER rather than promote/fail benchmark-blind, even though day
        # count and MDD alone would otherwise qualify (AC-4 edge case).
        return {"decision": "DEFERRED", "reason": None}

    # --- Window-complete evaluation: forward cumulative alpha vs SPY.
    #
    # AC-8 lookahead-bias rationale (the promotion-decision code site): this
    # comparison is only ever computed over paired_forward/paired_spy, which
    # by construction are observed strictly AFTER the candidate's admission
    # — data collected after admission post-dates the generating LLM's
    # training cutoff, making this the first memorization-free signal
    # anywhere in the Strategy Builder pipeline. A candidate can look
    # arbitrarily good on its backtest and still fail here.
    candidate_cum = _compound(paired_forward)
    spy_cum = _compound(paired_spy)
    alpha = candidate_cum - spy_cum

    if alpha >= 0.0:
        return {"decision": "PROMOTED", "reason": None}
    return {"decision": "FAILED", "reason": "forward_alpha_negative"}


# ---------------------------------------------------------------------------
# Daily tick — off-hours orchestration.
# ---------------------------------------------------------------------------


def run_incubation_tick() -> None:
    """Fetch forward data for every INCUBATING candidate, append new
    trading-day rows, and apply promotion/early-fail transitions.

    Off-hours, off-execution-path, CC-2 lazy-imported from app.py's daemon
    thread. D-1 never-raises: a fetch/processing error for one candidate is
    isolated and never aborts the tick for the others (mirrors
    perform_account_liquidation's per-symphony isolation precedent,
    DE-PANIC-STOP-CONFIRM-001).
    """
    candidates = database.get_incubating()
    if not candidates:
        # TICK8: an empty ledger makes zero Composer calls — not even the
        # shared SPY call, since there is nothing to compare it against.
        return

    spy_returns_pct = _fetch_spy_returns_pct()

    for row in candidates:
        try:
            _tick_one_candidate(row, spy_returns_pct)
        except Exception as exc:  # noqa: BLE001 — D-1 per-candidate isolation
            logger.warning(
                "run_incubation_tick: candidate %s failed this tick: %s",
                row.get("candidate_hash"),
                type(exc).__name__,
            )


def _fetch_spy_returns_pct() -> dict[str, float]:
    """Fetch the shared SPY benchmark series once per tick (pct scale).

    Same 100%-SPY tree construction as strategy_builder_engine.py:993-998.
    On any error, returns {} — candidate fetches still proceed
    benchmark-blind (TICK6); a SPY-only failure never aborts the tick.
    """
    spy_tree = symphony_schema.make_root(
        _SPY_TREE_NAME,
        "daily",
        [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
    )
    try:
        result = run_backtest(spy_tree)
    except Exception as exc:  # noqa: BLE001 — D-1, SPY fetch is best-effort
        logger.warning("run_incubation_tick: SPY fetch raised: %s", type(exc).__name__)
        return {}
    if result.error or not result.daily_returns:
        return {}
    return {d: r * 100.0 for d, r in result.daily_returns.items()}


def _tick_one_candidate(row: dict, spy_returns_pct: dict[str, float]) -> None:
    """Fetch + process one INCUBATING candidate's forward data for this tick."""
    candidate_hash_value = row["candidate_hash"]

    try:
        tree = json.loads(row["tree_json"])
    except (TypeError, ValueError) as exc:
        logger.warning(
            "run_incubation_tick: %s has an unparseable tree_json: %s",
            candidate_hash_value,
            type(exc).__name__,
        )
        return

    try:
        result = run_backtest(tree)
    except Exception as exc:  # noqa: BLE001 — classified as a fetch failure below
        logger.warning(
            "run_incubation_tick: %s fetch raised: %s",
            candidate_hash_value,
            type(exc).__name__,
        )
        _handle_fetch_outcome(candidate_hash_value, ok=False)
        return

    if result.error:
        # Detect a genuine grammar rejection (Composer 422 — the tree is no
        # longer valid) vs any other fetch failure, via the SAME
        # error-envelope parser plan_tree_compiler.py already uses — no
        # second parser.
        status = _parse_envelope_status(result.error)
        if status == 422:
            database.set_incubation_status(
                candidate_hash_value, "EXPIRED", "composer_422_tree_invalid"
            )
            return
        _handle_fetch_outcome(candidate_hash_value, ok=False)
        return

    _handle_fetch_outcome(candidate_hash_value, ok=True)
    _append_new_days(candidate_hash_value, result.daily_returns, spy_returns_pct)
    _evaluate_and_apply_promotion(candidate_hash_value, row["backtest_mdd_pct"])


def _handle_fetch_outcome(candidate_hash_value: str, *, ok: bool) -> None:
    """Record this tick's fetch outcome and apply the EXPIRED threshold policy.

    Storage (the consecutive-failure count) lives in database.py via
    record_incubation_fetch_outcome — a genuine fetch failure produces zero
    incubation_daily rows, so the count cannot be derived from that table,
    and it must survive across ticks/restarts (a plain in-memory counter
    would silently reset on every daemon restart). Policy (the
    INCUBATION_MAX_FETCH_FAILURES threshold) stays here, consistent with the
    project's storage-in-database/policy-in-advisors layering split.
    """
    count = database.record_incubation_fetch_outcome(candidate_hash_value, ok)
    if not ok and count >= INCUBATION_MAX_FETCH_FAILURES:
        database.set_incubation_status(candidate_hash_value, "EXPIRED", "fetch_failures_exhausted")


def _append_new_days(
    candidate_hash_value: str,
    daily_returns: dict[str, float],
    spy_returns_pct: dict[str, float],
) -> None:
    """Append every real trading day in this candidate's full-history backtest
    response.

    No pre-filter against a separately-queried "last recorded day" is needed
    for correctness: incubation_daily's UNIQUE(candidate_hash, trading_day)
    constraint + append_incubation_day's INSERT-OR-IGNORE contract make a
    re-submitted day a safe no-op at the DB layer (team ruling, 2026-07-25)
    — this loop IS the catch-up-after-gap mechanism, since a daemon down for
    N days yields N new date keys in one run_backtest response, backfilled
    here in a single call. Write volume stays small in practice: a candidate
    leaves database.get_incubating()'s result set once its window resolves
    (~INCUBATION_WINDOW_TRADING_DAYS), so the full-history resend never
    grows unbounded.
    """
    for iso_date, raw_return in daily_returns.items():
        try:
            trading_day = date.fromisoformat(iso_date)
        except ValueError:
            continue
        if not market_calendar.is_trading_day(trading_day):
            continue
        forward_return_pct = raw_return * 100.0
        spy_pct = spy_returns_pct.get(iso_date)
        database.append_incubation_day(candidate_hash_value, iso_date, forward_return_pct, spy_pct)


def _evaluate_and_apply_promotion(candidate_hash_value: str, backtest_mdd_pct: float) -> None:
    """Run promotion/early-fail evaluation for one candidate and apply any
    resulting status transition. CONTINUE/DEFERRED apply no status change —
    the candidate simply stays INCUBATING."""
    forward_pct, spy_pct = database.get_incubation_daily_series(candidate_hash_value)
    result = evaluate_promotion(
        forward_return_pct=forward_pct,
        spy_return_pct=spy_pct,
        backtest_mdd_pct=backtest_mdd_pct,
        days_observed=len(forward_pct),
    )
    decision = result["decision"]
    if decision in ("PROMOTED", "FAILED"):
        database.set_incubation_status(candidate_hash_value, decision, result["reason"])
