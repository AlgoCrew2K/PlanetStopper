"""Weekly Suggestions Orchestrator — Workstream B, plus the C.1/C.2 per-symphony
loop callers (feature-plans/advisor-weekly-suggestions.md).

This module is OFFLINE only (not on the 1-minute live execution path). It is the
single weekly entry point that wires three previously-unwired advisor engines to
real, unattended, recurring execution:

  1. ``strategy_builder_scheduler.run_weekly_build()`` — Strategy Builder
     (Component 4, already exists; this module does not modify it).
  2. ``run_weekly_asset_swap_suggestions()`` (AC-C2, new here) — enumerates live
     symphonies and calls ``advisors.asset_swap_engine.suggest_swaps(...)`` once
     per symphony with a ``reduce_correlation`` objective (v1 default).
  3. ``run_weekly_logic_change_suggestions()`` (AC-C1, new here) — enumerates
     live symphonies and calls
     ``advisors.logic_change_engine.suggest_logic_changes(...)`` once per
     symphony.

``run_weekly_suggestions()`` (AC-B1) calls all three in sequence, each wrapped in
its own D-1 try/except so one engine's failure never blocks the next.

Architecture constraints
------------------------
* This module MUST NOT be imported from ``alpha_bot_execution.py`` (off the live
  execution path, mirrors every other advisor engine's AC-X2-style constraint).
* This module never references the live-order-placement config flag —
  advisory-only, read + inline-backtest Composer endpoints only (via the
  UNCHANGED engines).
* ``suggest_swaps`` / ``suggest_logic_changes`` are the UNCHANGED engines
  (AC-C3) — this module is purely the enumeration/caller/loop layer that did
  not exist before this cycle.
* Every persisted row still carries ``is_advisory_only=1`` (forced inside
  ``database.insert_advisor_observation`` regardless of caller) — no new write
  path, no trade action.
* Bounded, not budget-metered: this module makes NO direct Anthropic API calls
  (unlike ``prism_scheduler.py``, which needs ``MAX_BUDGET_USD`` for its Claude
  spend) — its cost surface is Composer ``/backtest`` calls (rate-limited to
  1 req/s inside the engines) and Alpaca bar fetches. The budget-style guard
  here is ``_ASSET_SWAP_CANDIDATE_POOL_SIZE`` (bounds the per-symphony
  backtest count) plus each engine's own existing bound
  (``strategy_builder_scheduler.MAX_ATTEMPTS``,
  ``logic_change_engine.MAX_SUGGESTED_CANDIDATES``).

Invoke:
  python -m advisors.weekly_suggestions_scheduler

Reference: feature-plans/advisor-weekly-suggestions.md Workstreams B, C.1, C.2.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

# Calendar-day lookback for the asset-swap loop's correlation_data bar fetch.
# 90 calendar days is ~63 trading days, clearing correlation_diagnostic.py's
# THIN_DATA_THRESHOLD=30-observation floor with comfortable margin for
# weekends/holidays, while staying well inside a single weekly oneshot's
# Composer/Alpaca budget (mirrors lens_technicals.py's _get_bars pattern).
_CORRELATION_LOOKBACK_DAYS: int = 90

# Bounded candidate pool (deterministic alphabetical cap) for the weekly
# asset-swap loop, applied to the lens-covered universe union (see
# _build_base_candidate_pool: lens_technicals._PROXY_UNIVERSE ∪ live
# logic_holdings). Each pool member costs one Composer /backtest call
# (rate-limited to 1 req/s) inside suggest_swaps -- normally a no-op since
# _PROXY_UNIVERSE alone is 10 members, but bounds the union if live holdings
# push it over. Source: mirrors strategy_builder_engine.MAX_CANDIDATES_PER_RUN's
# bounding rationale.
_ASSET_SWAP_CANDIDATE_POOL_SIZE: int = 15

# Default LogicChangeObjective.objective_type for the unattended weekly sweep
# (AC-C1 -- not pinned by the RED tests; "a LogicChangeObjective default is
# fine"). reduce_drawdown is the most broadly protective default (every
# symphony benefits from tighter risk control absent an operator-specified
# target); a smarter per-symphony objective picker is a natural follow-up,
# out of this workstream's scope.
_DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE: str = "reduce_drawdown"

# Default SwapObjective.objective_type for the unattended weekly sweep.
# AC-C2 v1 scope boundary -- pinned by TestAssetSwapLoopObjectiveDefault.
_DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE: str = "reduce_correlation"


# ---------------------------------------------------------------------------
# Shared helper — enumerate live symphonies from bot_state
# ---------------------------------------------------------------------------


def _live_symphony_hashes(bot_state: dict) -> list:
    """Return the Composer-hash keys of every live symphony in bot_state.

    bot_state (database.load_state()) mixes symphony entries (dict values with
    a "name" key) with reserved top-level scalars (e.g. "date",
    "last_execution_mode") -- this is the same filter convention used
    throughout app.py (e.g. app.py:1156, app.py:2565).
    """
    return [k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v]


# ---------------------------------------------------------------------------
# AC-C1 — weekly logic-change suggestion loop
# ---------------------------------------------------------------------------


def run_weekly_logic_change_suggestions() -> None:
    """Enumerate live symphonies and call suggest_logic_changes once per symphony.

    AC-C1: enumerates database.load_state(), resolves each Composer hash (the
    bot_state keys ARE the hash already), fetches its score tree via
    symphony_logic.fetch_symphony_score, and calls
    advisors.logic_change_engine.suggest_logic_changes(symphony_id, score_tree,
    objective). Per-symphony D-1: one symphony's score-fetch failure or
    suggest_logic_changes exception never blocks the others. Never raises.
    """
    import database  # noqa: PLC0415 - CC-2 lazy, off-execution-path
    import symphony_logic  # noqa: PLC0415
    from advisors.logic_change_engine import (  # noqa: PLC0415
        LogicChangeObjective,
        suggest_logic_changes,
    )

    bot_state = database.load_state()
    symphony_hashes = _live_symphony_hashes(bot_state)

    for symphony_hash in symphony_hashes:
        try:
            score_tree = symphony_logic.fetch_symphony_score(symphony_hash)
            objective = LogicChangeObjective(
                objective_type=_DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE,
                measured_value=0.0,
                rationale=(
                    "Weekly automatic suggestion sweep (no operator-specified "
                    "objective; defaults to broad risk reduction)."
                ),
            )
            suggest_logic_changes(symphony_hash, score_tree, objective)
        except Exception as exc:  # noqa: BLE001 - D-1: contain, log class name only
            logger.warning(
                "run_weekly_logic_change_suggestions: symphony %s failed (%s)",
                symphony_hash,
                type(exc).__name__,
            )
            continue

    logger.info(
        "run_weekly_logic_change_suggestions: complete (%d symphonies attempted)",
        len(symphony_hashes),
    )


# ---------------------------------------------------------------------------
# AC-C2 — weekly asset-swap suggestion loop
# ---------------------------------------------------------------------------


def _closes_from_bars(bars: list | None) -> list:
    """Extract a close-price-like series from a bar list.

    Tolerates real Alpaca daily-bar dicts (``{"c": price, ...}``, per
    synthetic_history.fetch_bars) AND plain numeric sequences (as used by
    test doubles / any future bar-source shape) -- never raises on a
    malformed entry, just skips it.
    """
    closes: list = []
    for bar in bars or []:
        if isinstance(bar, dict):
            c = bar.get("c")
            if isinstance(c, (int, float)):
                closes.append(float(c))
        elif isinstance(bar, (int, float)):
            closes.append(float(bar))
    return closes


def _returns_from_closes(closes: list) -> list:
    """Convert a close-price series to daily percent returns.

    Returns [] when fewer than 2 closes are available (a return needs a prior
    close) or when a zero-valued prior close would divide-by-zero.
    """
    returns: list = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        returns.append((closes[i] - prev) / prev)
    return returns


def _build_correlation_data(tickers: set) -> dict:
    """Fetch bars for `tickers` and convert to a ticker -> daily-return-series
    dict, via synthetic_history.fetch_bars (read-only GET, no write verb).

    Returns {} for an empty ticker set or when the fetch/parse yields no
    usable series for any ticker -- callers (suggest_swaps) already handle an
    empty/degenerate correlation_data honestly (no reference series -> neutral
    scoring, never a crash).
    """
    if not tickers:
        return {}

    import synthetic_history  # noqa: PLC0415 - CC-2 lazy, off-execution-path

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=_CORRELATION_LOOKBACK_DAYS)
    bars_by_ticker = synthetic_history.fetch_bars(
        sorted(tickers), start_dt.isoformat(), end_dt.isoformat(), timeframe="1Day"
    )
    if not isinstance(bars_by_ticker, dict):
        return {}

    correlation_data: dict = {}
    for ticker, bars in bars_by_ticker.items():
        returns = _returns_from_closes(_closes_from_bars(bars))
        if returns:
            correlation_data[ticker] = returns
    return correlation_data


def _fetch_lens_scores() -> dict:
    """Read-only fetch of market-wide lens evidence, ONCE per weekly run.

    Lenses are market-wide, not per-symphony, so this is called once (not
    once per symphony) and the same lens_scores dict is passed to every
    suggest_swaps call in the loop below.

    Sourced from the nightly MARKET_LENS_CACHE bundle
    (database.get_latest_market_lens_cache()) -- NEVER a live lens-API fetch.
    The 5 lens producers are the nightly lens_pipeline's job (run once daily
    at 03:00); re-fetching them live here would blow the weekly run's bounded
    Composer/Alpaca budget and duplicate that pipeline's work.

    Honest degradation (never fabricates evidence): a cold cache (no row yet)
    or a row whose lenses are all available=False both degrade to {} --
    which _apply_lens_blend already treats as a no-op (falsy check, same
    as the pre-existing lens_scores=None path).
    """
    import database  # noqa: PLC0415 - CC-2 lazy, off-execution-path
    from advisors.asset_swap_engine import extract_lens_scores  # noqa: PLC0415

    try:
        cache_row = database.get_latest_market_lens_cache()
    except Exception as exc:  # noqa: BLE001 - D-1 (get_latest_market_lens_cache is
        # itself D-1 never-raising; this is defense-in-depth against an
        # unexpected future change, matching the belt-and-suspenders pattern
        # used elsewhere in this cycle, e.g. strategy_builder_scheduler's
        # guarded load_atlas_candidates call).
        logger.warning(
            "run_weekly_asset_swap_suggestions: lens cache read failed (%s)",
            type(exc).__name__,
        )
        return {}

    if not cache_row:
        return {}

    raw_response = cache_row.get("raw_response") or {}
    lenses = raw_response.get("lenses")
    if not isinstance(lenses, dict):
        return {}

    return extract_lens_scores(lenses)


def _build_base_candidate_pool(bot_state: dict) -> list:
    """Build the lens-covered candidate-pool base, ONCE per run.

    PM-routed follow-up (second live-E2E, 2026-07-12): the prior
    ``sorted(get_tradeable_set())[:_ASSET_SWAP_CANDIDATE_POOL_SIZE]`` sourcing
    was an alphabetical-first sample of the full ~12,748-symbol Alpaca
    tradeable universe -- structurally incapable of overlapping
    ``lens_technicals._PROXY_UNIVERSE`` (the 10 sector-ETF tickers the
    technicals lens actually scores), so ``lens_scores.get(candidate)`` always
    missed and ``lens_evidence`` stayed ``{}`` even with a real, correctly-
    parsed lens cache (E2E-confirmed).

    Fix: the pool is the LENS-COVERED universe --
    ``lens_technicals._PROXY_UNIVERSE`` unioned with every live symphony's
    ``logic_holdings`` (the bot_state field ``ai_advisor.py``'s technicals/
    fundamentals builders already read, e.g. ``ai_advisor.py:520-526`` --
    NOT the Composer score-tree structure ``extract_tickers`` reads). This
    makes swaps both sensible (real market-proxy / actually-held tickers, not
    an alphabetical accident) and lens-informed.

    ``universe_provider.get_tradeable_set()`` is deliberately NOT used to
    filter this pool -- broad correlation-screened discovery across the full
    tradeable universe is a documented future enhancement, out of this
    cycle's scope; intersecting against it here would reintroduce the same
    "lens-covered tickers get filtered out" failure mode this fix closes.

    Bounded to ``_ASSET_SWAP_CANDIDATE_POOL_SIZE`` (deterministic
    alphabetical sample) -- ``_PROXY_UNIVERSE`` alone is 10, so the cap is
    normally a no-op; it only bites if live holdings push the union past it.
    """
    from advisors.lens_technicals import _PROXY_UNIVERSE  # noqa: PLC0415

    pool: set = set(_PROXY_UNIVERSE)
    for entry in bot_state.values():
        if isinstance(entry, dict):
            for ticker in entry.get("logic_holdings", {}):
                if ticker:
                    pool.add(ticker)

    return sorted(pool)[:_ASSET_SWAP_CANDIDATE_POOL_SIZE]


def run_weekly_asset_swap_suggestions() -> None:
    """Enumerate live symphonies and call suggest_swaps once per symphony.

    AC-C2: same enumeration as C.1; assembles the ticker-level return-series
    correlation_data via a synthetic_history.fetch_bars-style step over a
    candidate pool sourced from the lens-covered universe (see
    _build_base_candidate_pool); objective defaults to reduce_correlation (v1
    scope boundary). Per-symphony D-1: one symphony's score-fetch/bar-fetch
    failure or suggest_swaps exception never blocks the others. Never raises.

    lens_scores is wired through _fetch_lens_scores() (read-only
    MARKET_LENS_CACHE read, once per run -- see that function's docstring) and
    passed to every suggest_swaps call, now that Workstream D (the lens-blend
    fix) is GREEN -- this closes the "fixed math, dead in production" gap
    (extract_lens_scores/_apply_lens_blend were previously unreachable via any
    real weekly path).

    Per-symphony pool exclusion: each symphony's own candidate pool excludes
    THAT symphony's own held ticker(s) -- extracted from ITS OWN Composer
    score_tree via extract_tickers, the same source the primary_ticker anchor
    already uses -- so a symphony is never offered its own current holding as
    a "new" swap candidate (no swap-into-self). A ticker held by a DIFFERENT
    symphony remains a valid candidate for this one (no cross-symphony
    conflict) -- this mirrors, at the pool-construction level, the
    engine's own existing per-symphony filter (suggest_swaps already skips
    `candidate_asset in present_tickers`, asset_swap_engine.py) --
    defense-in-depth / explicit-by-construction, not a new behavioral class.
    """
    import database  # noqa: PLC0415 - CC-2 lazy, off-execution-path
    import symphony_logic  # noqa: PLC0415
    from advisors.asset_swap_engine import (  # noqa: PLC0415
        SwapObjective,
        extract_tickers,
        suggest_swaps,
    )

    bot_state = database.load_state()
    symphony_hashes = _live_symphony_hashes(bot_state)

    if not symphony_hashes:
        return

    base_pool = _build_base_candidate_pool(bot_state)

    # Market-wide lens evidence, fetched ONCE for the whole run (not per
    # symphony) -- see _fetch_lens_scores(). Falsy (None/{}) degrades to the
    # pre-existing no-op path inside _apply_lens_blend.
    lens_scores = _fetch_lens_scores()

    for symphony_hash in symphony_hashes:
        try:
            score_tree = symphony_logic.fetch_symphony_score(symphony_hash)
            held_tickers = extract_tickers(score_tree)
            # v1 simplification: the symphony's own alphabetically-first held
            # ticker anchors the correlation target. Picking the TRUE most-
            # correlated pair is correlation_diagnostic.py's job (a separate,
            # more sophisticated analysis) -- out of this loop-wiring
            # workstream's scope (AC-C3: the engines are unchanged).
            primary_ticker = sorted(held_tickers)[0] if held_tickers else None

            # Per-symphony exclusion (no swap-into-self) -- see docstring.
            candidate_pool = [t for t in base_pool if t not in held_tickers]

            needed_tickers = set(held_tickers) | set(candidate_pool)
            correlation_data = _build_correlation_data(needed_tickers)

            objective = SwapObjective(
                objective_type=_DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE,
                target_pair=(primary_ticker, primary_ticker) if primary_ticker else None,
                measured_value=0.0,
            )

            suggest_swaps(
                symphony_hash,
                score_tree,
                objective,
                correlation_data,
                candidate_pool,
                lens_scores=lens_scores,
            )
        except Exception as exc:  # noqa: BLE001 - D-1: contain, log class name only
            logger.warning(
                "run_weekly_asset_swap_suggestions: symphony %s failed (%s)",
                symphony_hash,
                type(exc).__name__,
            )
            continue

    logger.info(
        "run_weekly_asset_swap_suggestions: complete (%d symphonies attempted)",
        len(symphony_hashes),
    )


# ---------------------------------------------------------------------------
# AC-B1 — weekly orchestrator: all three engines, in sequence, D-1 isolated
# ---------------------------------------------------------------------------


def run_weekly_suggestions() -> None:
    """Run all three weekly advisor engines in sequence.

    AC-B1: calls, IN THIS ORDER, each wrapped in its own D-1 try/except (one
    engine's failure never blocks the next):
      1. strategy_builder_scheduler.run_weekly_build()
      2. run_weekly_asset_swap_suggestions()
      3. run_weekly_logic_change_suggestions()

    AC-B2: per-engine same-ISO-week idempotency is each engine's OWN
    responsibility (strategy_builder_scheduler._already_ran_this_week() for
    (1); the swap/logic engines persist ASSET_SWAP/LOGIC_CHANGE rows that a
    role-filtered dedup guard can query the same way -- this orchestrator adds
    no conflicting outer guard that would prevent that).

    Never raises (D-1), even if all three engines fail.
    """
    import advisors.strategy_builder_scheduler as sb_sched  # noqa: PLC0415

    try:
        sb_sched.run_weekly_build()
    except Exception as exc:  # noqa: BLE001 - D-1
        logger.warning(
            "run_weekly_suggestions: strategy_builder engine failed (%s)", type(exc).__name__
        )

    try:
        run_weekly_asset_swap_suggestions()
    except Exception as exc:  # noqa: BLE001 - D-1
        logger.warning("run_weekly_suggestions: asset_swap engine failed (%s)", type(exc).__name__)

    try:
        run_weekly_logic_change_suggestions()
    except Exception as exc:  # noqa: BLE001 - D-1
        logger.warning(
            "run_weekly_suggestions: logic_change engine failed (%s)", type(exc).__name__
        )

    logger.info("run_weekly_suggestions: weekly sweep complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekly_suggestions()
