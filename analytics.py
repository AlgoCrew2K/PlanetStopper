"""
analytics.py — Performance tab data layer.

Loads per-day post_mortem_<YYYY-MM-DD>.json snapshots written by
reporting.py:generate_eod_snapshot and exposes aggregate / per-symphony
return series plus quantstats-derived risk metrics for the dashboard.

Producer schema (reporting.py:generate_eod_snapshot, on-disk):
    {
        "date": "YYYY-MM-DD",
        "summary": {...},
        "tomorrow_target_holdings": {...},
        "triggers": [
            {
                "symphony_name":           str,    -> internal "symphony_id" key
                "symphony_value":          float,  -> internal "value"
                "shadow_return":           float,  -> internal "live_ret"
                "exit_return":             float,  -> internal "f_ret"
                "account_id":              str,
                "exit_reason":             str,
                "attempted_trigger_level": float,
                "shadow_hwm":              float,
                "saved_pct_guard_alpha":   float,
                "saved_dollars":           float,
                "hwm_at_trigger":          float,
                "time_triggered":          str,
                "symphony_vol":            float,
                "strategy_params":         dict,
                "next_day_holdings":       list,
            },
            ...
        ],
    }

Internal shape (DV1 binding contract):
    {date_str: {symphony_id: {"live_ret", "f_ret", "value", ...full payload}}}
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
from datetime import UTC, date

import database

# ---------------------------------------------------------------------------
# Producer -> internal field-name mapping (single source of truth)
# ---------------------------------------------------------------------------
# Source: reporting.py:generate_eod_snapshot trigger dict construction.
# If reporting.py renames any of these, the canary test
# (test_analytics_consumes_full_reporting_schema_without_keyerror) catches it.
_PRODUCER_SYMPHONY_ID = "symphony_name"  # -> dict key
_PRODUCER_VALUE = "symphony_value"  # -> "value"
_PRODUCER_LIVE_RET = "shadow_return"  # -> "live_ret"
_PRODUCER_F_RET = "exit_return"  # -> "f_ret"

# Filename pattern + extraction regex.
_POST_MORTEM_GLOB = "post_mortem_*.json"
_POST_MORTEM_DATE_RE = re.compile(r"post_mortem_(\d{4}-\d{2}-\d{2})\.json$")

# Canonical post-mortem directory — anchored to project root.
# Pass as base_dir= to load_post_mortem_history / get_history_with_cache_invalidation
# / get_history_summary in production callers (app.py).  Default "." is kept so that
# tests using monkeypatch.chdir or tmp_path still work unchanged.
_POST_MORTEMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "post_mortems")

# Module-level cache for get_history_with_cache_invalidation.
# Key: (latest_mtime_seen, days, base_dir). Reload on any change.
_HISTORY_CACHE: dict = {"key": None, "data": None}

# Minimum finite observations required for quantstats. < 2 => insufficient data.
# Source: DV1 binding contract.
_MIN_QUANTSTATS_OBSERVATIONS = 2

# Trading days per year — the industry-standard annualization basis for daily
# volatility (sqrt-time rule). Matches the 252-day basis quantstats uses in
# compute_quantstats_metrics' `volatility` key. Source: ux-design-deliverable.md §Change 4.
_ANNUALIZATION_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# load_post_mortem_history
# ---------------------------------------------------------------------------


def load_post_mortem_history(days: int = 60, base_dir: str = ".") -> dict:
    """
    Load up to `days` most-recent post_mortem_<YYYY-MM-DD>.json files from
    base_dir and return the DV1 internal shape:

        {date_str: {symphony_id: {"live_ret", "f_ret", "value", ...trigger}}}

    Producer-field mapping applied per trigger entry:
        symphony_name  -> dict key (symphony_id)
        symphony_value -> "value"
        shadow_return  -> "live_ret"
        exit_return    -> "f_ret"

    All other trigger fields are carried forward verbatim.

    Resilience:
    - Missing / unreadable files are silently skipped.
    - Malformed JSON is silently skipped (does NOT raise).
    - Files without a parseable date in the filename are skipped.
    - When more than `days` files exist, the `days` most-recent (by the date
      embedded in the filename) are kept.
    """
    if days <= 0:
        return {}

    pattern = os.path.join(base_dir, _POST_MORTEM_GLOB)
    candidates: list[tuple[str, str]] = []  # (date_str, fpath)
    for fpath in glob.glob(pattern):
        fname = os.path.basename(fpath)
        m = _POST_MORTEM_DATE_RE.search(fname)
        if not m:
            continue
        candidates.append((m.group(1), fpath))

    # Sort by date_str ascending (ISO YYYY-MM-DD sorts lexicographically),
    # then take the `days` most-recent.
    candidates.sort(key=lambda pair: pair[0])
    selected = candidates[-days:]

    history: dict = {}
    for date_str, fpath in selected:
        try:
            with open(fpath, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # Silently skip malformed / unreadable files per contract.
            continue

        if not isinstance(payload, dict):
            continue
        triggers = payload.get("triggers")
        if not isinstance(triggers, list):
            continue

        day_entries: dict = {}
        for trig in triggers:
            if not isinstance(trig, dict):
                continue
            symphony_id = trig.get(_PRODUCER_SYMPHONY_ID)
            if symphony_id is None:
                continue
            # Build the internal-shape entry: carry forward the full producer
            # payload, then overlay the internal-shape keys.
            entry = dict(trig)
            # Pull producer values defensively; missing producer fields surface
            # as missing internal keys (consumers must tolerate absence).
            if _PRODUCER_LIVE_RET in trig:
                entry["live_ret"] = trig[_PRODUCER_LIVE_RET]
            if _PRODUCER_F_RET in trig:
                entry["f_ret"] = trig[_PRODUCER_F_RET]
            if _PRODUCER_VALUE in trig:
                entry["value"] = trig[_PRODUCER_VALUE]
            day_entries[symphony_id] = entry

        history[date_str] = day_entries

    return history


# ---------------------------------------------------------------------------
# list_available_symphonies
# ---------------------------------------------------------------------------


def list_available_symphonies(history: dict) -> list[str]:
    """Sorted, de-duplicated list of every symphony_id appearing in `history`."""
    seen: set[str] = set()
    for day_entries in history.values():
        if not isinstance(day_entries, dict):
            continue
        seen.update(day_entries.keys())
    return sorted(seen)


# ---------------------------------------------------------------------------
# compute_aggregate_returns
# ---------------------------------------------------------------------------


def compute_aggregate_returns(
    history: dict,
    weight_by: str = "value",
) -> tuple[list[str], list[float], list[float]]:
    """
    Aggregate per-day returns across symphonies, weighted by `weight_by`
    (internal-shape field, default "value").

    Per-day formula:
        weighted_return = sum(ret_i * weight_i) / sum(weight_i)

    Edge cases:
    - A symphony missing the weight field on a given day is skipped FOR THAT
      DAY (other symphonies that day still contribute).
    - A day where no symphony has a usable weight is omitted entirely from
      the output.
    - Empty history -> three empty parallel lists.

    Returns (dates_sorted_ascending, live_returns, shadow_returns).
    Note: the parameter ordering of the returned tuple follows the DV1
    contract — the second list is live returns (from live_ret), the third
    is the Planet Stopper-exited / shadow returns (from f_ret).
    """
    out_dates: list[str] = []
    out_live: list[float] = []
    out_f: list[float] = []

    for date_str in sorted(history.keys()):
        day_entries = history[date_str]
        if not isinstance(day_entries, dict):
            continue

        weight_sum = 0.0
        live_wsum = 0.0
        f_wsum = 0.0
        contributed = 0

        for entry in day_entries.values():
            if not isinstance(entry, dict):
                continue
            if weight_by not in entry:
                # Symphony missing weight on this day -> skip for this day.
                continue
            try:
                w = float(entry[weight_by])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(w):
                continue

            live = entry.get("live_ret")
            f = entry.get("f_ret")
            if live is None or f is None:
                continue
            try:
                live_f = float(live)
                f_f = float(f)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(live_f) and math.isfinite(f_f)):
                continue

            weight_sum += w
            live_wsum += live_f * w
            f_wsum += f_f * w
            contributed += 1

        if contributed == 0 or weight_sum == 0.0:
            # No usable contributions this day -> omit.
            continue

        out_dates.append(date_str)
        out_live.append(live_wsum / weight_sum)
        out_f.append(f_wsum / weight_sum)

    return out_dates, out_live, out_f


# ---------------------------------------------------------------------------
# compute_per_symphony_returns
# ---------------------------------------------------------------------------


def compute_per_symphony_returns(
    history: dict,
    symphony_id: str,
) -> tuple[list[str], list[float], list[float]]:
    """
    Extract one symphony's (live_ret, f_ret) series across the dates where it
    appears, sorted chronologically. Days where the symphony is absent are
    simply omitted.

    Returns (dates_sorted, live_returns, shadow_returns) — parallel lists.
    """
    out_dates: list[str] = []
    out_live: list[float] = []
    out_f: list[float] = []

    for date_str in sorted(history.keys()):
        day_entries = history[date_str]
        if not isinstance(day_entries, dict):
            continue
        entry = day_entries.get(symphony_id)
        if not isinstance(entry, dict):
            continue
        live = entry.get("live_ret")
        f = entry.get("f_ret")
        if live is None or f is None:
            continue
        try:
            live_f = float(live)
            f_f = float(f)
        except (TypeError, ValueError):
            continue
        out_dates.append(date_str)
        out_live.append(live_f)
        out_f.append(f_f)

    return out_dates, out_live, out_f


# ---------------------------------------------------------------------------
# compute_quantstats_metrics
# ---------------------------------------------------------------------------


def compute_quantstats_metrics(returns_series: list[float], freq: str = "D") -> dict:
    """
    Compute risk metrics over a daily return series using quantstats.

    Keys returned (always present):
        total_return, annualized_return, sharpe, sortino,
        max_drawdown, calmar, win_rate, volatility.

    `volatility` is annualized volatility (Phase 2 dashboard extension): the
    sample standard deviation of the daily fraction-scale return series scaled
    to a 252-trading-day year. It is returned in FRACTION scale (e.g. 0.035 =
    3.5% annual vol) consistent with the other fraction-scale metrics.

    NaN / Inf inputs are filtered out before computation. If fewer than
    _MIN_QUANTSTATS_OBSERVATIONS (=2) finite values remain, every metric is
    returned as None ("insufficient data" signal for the UI).

    Sharpe / sortino are computed with annualize=False so the test's
    plausible-range guard ([-5, 5] for tame daily series) holds without
    forcing a specific risk-free or annualization basis on callers.
    The `freq` parameter is accepted for forward compatibility; quantstats
    infers frequency from the DatetimeIndex.
    """
    metric_keys = (
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
        "volatility",
    )
    none_result = {k: None for k in metric_keys}

    if not returns_series:
        return none_result

    # Filter NaN / Inf upfront — operator contract is "no NaN/Inf in output".
    finite_vals: list[float] = []
    for v in returns_series:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            finite_vals.append(fv)

    if len(finite_vals) < _MIN_QUANTSTATS_OBSERVATIONS:
        return none_result

    # Lazy imports — keep module import cheap for non-metrics code paths.
    import pandas as pd
    import quantstats.stats as qs_stats

    # The caller passes percent-scale returns (e.g. -0.357 meaning -0.357%).
    # quantstats and our own total_return/CAGR/MDD formulas expect fraction-scale
    # (e.g. -0.00357). Divide by 100 here so all downstream metric calls see
    # the correct scale. Sharpe/Sortino/win_rate are ratio-based and
    # scale-invariant, but total_return, CAGR, and max_drawdown are NOT.
    fraction_vals = [v / 100.0 for v in finite_vals]

    # Build a daily DatetimeIndex; quantstats infers periodicity from it.
    idx = pd.date_range("2000-01-01", periods=len(fraction_vals), freq="D")
    series = pd.Series(fraction_vals, index=idx, dtype=float)

    def _safe(call) -> float | None:
        """Run a metric call; convert NaN/Inf/errors to None."""
        try:
            result = call()
        except Exception:
            return None
        try:
            result_f = float(result)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(result_f):
            return None
        return result_f

    metrics: dict = {}

    # total_return: compounded sum (1 + r_i).prod() - 1
    metrics["total_return"] = _safe(lambda: float((1.0 + series).prod() - 1.0))

    # annualized_return: quantstats.cagr (252-day basis by default)
    metrics["annualized_return"] = _safe(lambda: qs_stats.cagr(series))

    # sharpe / sortino: annualize=False to stay in the [-5, 5] plausibility
    # band for daily tame series. Risk-free defaults to 0.
    metrics["sharpe"] = _safe(lambda: qs_stats.sharpe(series, annualize=False))
    metrics["sortino"] = _safe(lambda: qs_stats.sortino(series, annualize=False))

    # max_drawdown: quantstats expects price-like or return-like input;
    # passing returns yields a <= 0 drawdown value (drawdown convention).
    metrics["max_drawdown"] = _safe(lambda: qs_stats.max_drawdown(series))

    # calmar: cagr / |max_drawdown|. quantstats.calmar uses CAGR internally.
    metrics["calmar"] = _safe(lambda: qs_stats.calmar(series))

    # win_rate: fraction of positive observations in [0, 1].
    metrics["win_rate"] = _safe(lambda: float((series > 0).mean()))

    # volatility: annualized std dev (252-day basis) in fraction scale. quantstats
    # operates on the same fraction-scale `series` as the other metrics, so no extra
    # scaling is needed — the result is fraction-scale (e.g. 0.035 = 3.5% annual vol).
    metrics["volatility"] = _safe(lambda: qs_stats.volatility(series))

    return metrics


# ---------------------------------------------------------------------------
# compute_portfolio_annualized_vol
# ---------------------------------------------------------------------------


def compute_portfolio_annualized_vol(
    portfolio_daily_returns_pct: list[float],
) -> float | None:
    """Annualized volatility of the COMBINED portfolio daily return series.

    The caller passes the portfolio's value-weighted per-day aggregate return
    series (e.g. from get_portfolio_daily_returns_from_shadow), NOT per-symphony
    returns. Computing vol on the combined series captures inter-symphony
    correlations; averaging per-symphony vols does not (Markowitz 1952).

    Formula: sample_std(returns_frac, ddof=1) * sqrt(252), returned in FRACTION
    scale (e.g. 0.035 = 3.5% annual vol) to match the other fraction-scale
    metrics in compute_quantstats_metrics.

    Inputs are percent-scale daily returns (e.g. 0.20 = 0.20%); they are divided
    by 100 to fraction scale before the std. Returns None when fewer than
    _MIN_QUANTSTATS_OBSERVATIONS finite observations remain.
    """
    fracs = [v / 100.0 for v in portfolio_daily_returns_pct if _is_finite_number(v)]
    if len(fracs) < _MIN_QUANTSTATS_OBSERVATIONS:
        return None

    n = len(fracs)
    mean = sum(fracs) / n
    variance = sum((r - mean) ** 2 for r in fracs) / (n - 1)  # ddof=1 (sample)
    vol = math.sqrt(variance) * math.sqrt(_ANNUALIZATION_TRADING_DAYS)

    if not math.isfinite(vol):
        return None
    return vol


def _is_finite_number(value: object) -> bool:
    """True when value coerces to a finite float (filters NaN/Inf/None/garbage)."""
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# M1 data-layer helpers — per-symphony and portfolio TC / CR / MDD
# ---------------------------------------------------------------------------
# Data-source contract (binding):
#   If-held side sourced from Composer symphony-stats-meta fields.
#   Dry-run side sourced from bot_state (Planet Stopper shadow tracking).
#   Network-free: callers pass already-fetched data; no fetch_symphony_stats calls here.


def get_symphony_today_change(
    sym_dict: dict,
    bot_state_entry: dict | None,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """
    Per-symphony Today's Change.

    if_held: last_percent_change * 100 (Composer decimal -> percent).
    dry_run: sourced in priority order:
      1. Triggered symphony: bot_state_entry["current_return"] (engine-stored pct).
      2. Shadow history row for (symphony_id, trading_day) when trading_day is explicit
         (kwarg or sym_dict["trading_day"]) — M1F live path; None when no row (AC-M1F.3.5).
      3. Fallback to if_held when trading_day was not explicitly provided and no shadow
         row exists — preserves pre-M1F semantics for callers that don't inject trading_day.
    trading_day: override for today; defaults to sym_dict["trading_day"] then today.
    db_path: override DB file path (for tests).
    """
    if_held = float(sym_dict["last_percent_change"]) * 100.0

    symphony_id = sym_dict.get("id")
    _trading_day = trading_day or sym_dict.get("trading_day")
    if not _trading_day:
        from datetime import datetime as _dt

        _trading_day = _dt.now(UTC).strftime("%Y-%m-%d")

    dry_run: float | None = None
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        row = _load_latest_shadow_row_for_analytics(symphony_id, _trading_day, _db_file)
        if row is not None:
            dry_run = float(row["shadow_return"])

    return {"if_held": if_held, "dry_run": dry_run}


# Patched by tests to redirect DB queries to tmp_path databases.
DB_FILE: str | None = None


def _get_shadow_db_file() -> str:
    """Return the shadow_history DB file path; honours test-time DB_FILE override."""
    if DB_FILE is not None:
        return DB_FILE
    import database as _db

    return _db.DB_FILE


def _load_latest_shadow_row_for_analytics(
    symphony_id: str, trading_day: str, db_file: str
) -> dict | None:
    """Read the most-recent shadow_history row for (symphony_id, trading_day)."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM shadow_history "
            "WHERE symphony_id = ? AND trading_day = ? "
            "ORDER BY ts_utc DESC LIMIT 1",
            (symphony_id, trading_day),
        ).fetchone()
        conn.close()
        return dict(row) if row is not None else None
    except Exception:
        return None


# AC-3: sentinel cache-key component for a legacy shadow_history table that has
# no position_epoch column at all (pre-migration DB shape). Distinct from a real
# epoch string and from None (which is a legitimate NULL epoch on a migrated DB).
_LEGACY_NO_EPOCH_COLUMN = "__legacy_no_epoch_column__"


def _get_shadow_cumulative_trajectory(symphony_id: str, db_file: str) -> list[float] | None:
    """Return ordered list of per-day EOD shadow_return values for chain-link CR.

    Day-row selection: last row per trading_day by ts_utc (AC-M1F.3.2).
    Returns None when fewer than 2 distinct trading days exist.

    AC-3: the query is scoped to the CURRENT position epoch. A Composer
    symphony_id is long-lived and Planet Stopper opens/exits/re-enters positions under
    it; an epoch-blind query would chain-link a prior position's returns into the
    new position. The current epoch is self-selected as the position_epoch of the
    latest row by ts_utc, and rows are filtered to it with `IS` so legacy
    NULL-epoch rows (pre-migration) all match and form one legacy segment.

    Epoch boundary granularity = session: an intraday same-symphony re-entry
    shares one epoch (the engine stamps the epoch at the wipe_transient_state /
    fresh-entry boundary and does not record intraday position rotation). This is
    a deliberate, documented limitation — deterministic, not a heuristic.

    Result is cached per (symphony_id, today, db_file, resolved_epoch) in
    database._shadow_cr_cache; the resolved epoch is part of the key so a
    trajectory cached for an earlier epoch is never served for a later one.
    Invalidated on new row write (record_shadow_observation).
    """
    import sqlite3
    from datetime import datetime as _dt

    import database as _db

    today = _dt.now(UTC).strftime("%Y-%m-%d")

    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        # Resolve the current epoch: the position_epoch of the latest row by
        # ts_utc. A legacy table has no position_epoch column -> OperationalError;
        # treat that as the single legacy segment.
        try:
            epoch_row = conn.execute(
                "SELECT position_epoch FROM shadow_history "
                "WHERE symphony_id = ? ORDER BY ts_utc DESC LIMIT 1",
                (symphony_id,),
            ).fetchone()
            has_epoch_column = True
            current_epoch = epoch_row[0] if epoch_row is not None else None
        except sqlite3.OperationalError:
            has_epoch_column = False
            current_epoch = None

        cache_key = (
            symphony_id,
            today,
            db_file,
            current_epoch if has_epoch_column else _LEGACY_NO_EPOCH_COLUMN,
        )
        cached = _db._shadow_cr_cache.get(cache_key)
        if cached is not None:
            conn.close()
            return cached  # type: ignore[return-value]

        if has_epoch_column:
            # `IS` (not `=`) so a NULL current epoch matches NULL legacy rows.
            rows = conn.execute(
                "SELECT trading_day, shadow_return "
                "FROM shadow_history "
                "WHERE symphony_id = ? "
                "  AND position_epoch IS ( "
                "        SELECT position_epoch FROM shadow_history s3 "
                "        WHERE s3.symphony_id = ? "
                "        ORDER BY s3.ts_utc DESC LIMIT 1) "
                "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
                "                WHERE s2.symphony_id = shadow_history.symphony_id "
                "                  AND s2.trading_day = shadow_history.trading_day) "
                "ORDER BY trading_day ASC",
                (symphony_id, symphony_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT trading_day, shadow_return "
                "FROM shadow_history "
                "WHERE symphony_id = ? "
                "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
                "                WHERE s2.symphony_id = shadow_history.symphony_id "
                "                  AND s2.trading_day = shadow_history.trading_day) "
                "ORDER BY trading_day ASC",
                (symphony_id,),
            ).fetchall()
        conn.close()
    except Exception:
        return None

    if len(rows) < 2:
        return None

    result = [float(r[1]) for r in rows]
    _db._shadow_cr_cache[cache_key] = result  # type: ignore[assignment]
    return result


def _get_shadow_divergence_trajectory(
    symphony_id: str, db_file: str
) -> list[list[tuple[float, float]]] | None:
    """Return the symphony's LIFETIME per-day EOD ``(shadow_return, current_return)``
    pairs GROUPED BY position epoch — the inputs to the EPOCH-ADDITIVE guard-alpha
    DIVERGENCE formula (semantic B).

    Return shape: a list of epoch GROUPS in chronological order; each group is that
    epoch's ordered list of ``(shadow_return, current_return)`` per-day pairs. The
    grouping is load-bearing for semantic B — see WHY EPOCH-ADDITIVE below.

    LIFETIME (cross-epoch) scope — DATA-CORRECTNESS-AUDIT C-1 fix (AC-1).
    ``database.wipe_transient_state`` resets a triggered symphony's
    ``position_epoch`` at the next market open, so a guard-trigger day's divergence
    is stranded in an OLD epoch while the latest epoch is a fresh 1-day (or flat
    multi-day) window with zero divergence. Scoping the divergence trajectory to the
    latest epoch (the prior behaviour) therefore reported a structural 0.00% Guard
    Alpha for EVERY symphony that ever protected the operator. This function selects
    the last row per trading_day by ts_utc across ALL epochs, ordered by trading_day,
    grouped into contiguous epoch runs.

    WHY EPOCH-ADDITIVE (semantic B — chain the GUARD's divergence, NOT a prior
    position's MARKET returns): the lifetime Guard Alpha is the SUM of each epoch's
    OWN divergence ``(prod_shadow_E - prod_current_E)``, computed inside that epoch's
    frame, NOT a single global product across all epochs. A single global product
    would multiply a prior epoch's ``prod_shadow`` into a later epoch's factors,
    letting a later epoch's market move (even one where the guard did nothing)
    rescale a prior position's already-realized guard saving — re-chaining market
    returns across the reset, the exact thing AC-1 forbids. Under B an untriggered
    epoch (shadow == current every day) contributes exactly 0 to the sum, so a
    never-triggered symphony has lifetime Guard Alpha == 0 by construction.

    Distinct from ``_get_shadow_cumulative_trajectory`` (the shadow-ONLY absolute
    series), which MUST stay scoped to the latest epoch: splicing the absolute
    shadow level across epochs WOULD chain a prior position's market returns. That
    function and its epoch-segmentation tests are intentionally left unchanged; only
    the divergence-pair trajectory goes lifetime.

    Returns None when fewer than 2 distinct trading days exist in total (no recorded
    divergence yet — both series coincide).
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        # Lifetime scope: NO position_epoch filter. The MAX(ts_utc) per
        # (symphony_id, trading_day) collapses each day to its EOD row; a trading_day
        # belongs to exactly one epoch, so the cross-epoch series is the ordered
        # concatenation of every epoch's EOD rows. We also SELECT position_epoch so
        # the rows can be grouped into epoch frames for the additive formula. A
        # missing position_epoch column (pre-migration DB) raises OperationalError;
        # the fallback query treats the whole history as one legacy epoch.
        try:
            rows = conn.execute(
                "SELECT trading_day, shadow_return, current_return, position_epoch "
                "FROM shadow_history "
                "WHERE symphony_id = ? "
                "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
                "                WHERE s2.symphony_id = shadow_history.symphony_id "
                "                  AND s2.trading_day = shadow_history.trading_day) "
                "ORDER BY trading_day ASC",
                (symphony_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Legacy schema with no position_epoch column — one implicit epoch.
            rows = [
                (r[0], r[1], r[2], None)
                for r in conn.execute(
                    "SELECT trading_day, shadow_return, current_return "
                    "FROM shadow_history "
                    "WHERE symphony_id = ? "
                    "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
                    "                WHERE s2.symphony_id = shadow_history.symphony_id "
                    "                  AND s2.trading_day = shadow_history.trading_day) "
                    "ORDER BY trading_day ASC",
                    (symphony_id,),
                ).fetchall()
            ]
        conn.close()
    except Exception:
        return None

    if len(rows) < 2:
        return None

    # Group the day-ordered rows into contiguous epoch runs (a label change — including
    # to/from NULL — starts a new group). Matches how the engine re-stamps a fresh
    # epoch per position; a repeated label after a gap would (correctly) form a new run.
    groups: list[list[tuple[float, float]]] = []
    _sentinel = object()  # never equal to a real epoch label or None
    current_label: object = _sentinel
    for _trading_day, shadow_r, current_r, epoch_label in rows:
        if epoch_label != current_label:
            groups.append([])
            current_label = epoch_label
        groups[-1].append((float(shadow_r), float(current_r)))
    return groups


def get_symphony_cumulative_return(
    sym_dict: dict,
    bot_state_entry: dict | None,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """
    Per-symphony Cumulative Return.

    if_held: simple_return UNLESS (simple_return == 0.0 AND net_deposits == 0.0),
             in which case falls back to time_weighted_return (anomalous withdrawn/re-funded
             symphony where simple_return would be misleadingly zero).
    dry_run: held series anchored + the guard-alpha DIVERGENCE over the shadow window.
             When >= 2 distinct shadow trading days exist,
                 dry_run = if_held + (prod_shadow - prod_current) * 100
             where prod_shadow = ∏(1 + shadow_return_day/100) and
             prod_current = ∏(1 + current_return_day/100) over the current epoch's
             per-day EOD rows. shadow_return is the bot's series (frozen at the exit
             level once the guard triggers); current_return is the held series (the
             position continuing to hold). Their compounded DIFFERENCE is the guard's
             cumulative effect — exactly what `Guard Alpha = dry_run - if_held` renders.
             This is NOT the absolute shadow cumulative (prod_shadow - 1): for an
             untriggered symphony shadow_return == current_return every day, so
             prod_shadow == prod_current and the divergence is exactly 0 — dry_run ==
             if_held, no phantom alpha. When fewer than 2 shadow days exist (or no
             shadow rows), dry_run = if_held (no recorded divergence yet).

    Returns {"if_held": None, "dry_run": None} when simple_return is None (missing data).

    "_twr_fallback": True is set when the TWR path is taken (simple_return == 0.0
    AND net_deposits == 0.0). This flag is consumed by _value_weighted_portfolio to
    exclude the symphony from the portfolio VW aggregate (F4 / CA-3 fix): the TWR
    fallback is defensible per-card but a 318% outlier pollutes the portfolio figure.
    Per-symphony if_held is still the correct TWR*100 value for the card display.
    """
    if sym_dict.get("simple_return") is None:
        return {"if_held": None, "dry_run": None}
    simple_return = float(sym_dict["simple_return"])
    net_deposits = float(sym_dict["net_deposits"])
    _twr_fallback = False
    if simple_return == 0.0 and net_deposits == 0.0:
        if_held = float(sym_dict["time_weighted_return"]) * 100.0
        _twr_fallback = True
    else:
        if_held = simple_return * 100.0

    symphony_id = sym_dict.get("id")

    # Default: no shadow divergence recorded — bot series equals held series.
    dry_run: float = if_held
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        trajectory = _get_shadow_divergence_trajectory(symphony_id, _db_file)
        if trajectory is not None:
            # Lifetime guard alpha = EPOCH-ADDITIVE sum of each epoch's own divergence
            # (semantic B). Each epoch's divergence is computed in its OWN frame
            # (products reset per epoch) so a later position's market move never
            # rescales a prior epoch's realized guard saving. Untriggered days
            # (shadow == current) contribute nothing; an untriggered epoch contributes
            # exactly 0, so a never-triggered symphony nets 0 across all epochs.
            lifetime_divergence = 0.0
            for epoch_pairs in trajectory:
                product_shadow = 1.0
                product_current = 1.0
                for shadow_r, current_r in epoch_pairs:
                    product_shadow *= 1.0 + shadow_r / 100.0
                    product_current *= 1.0 + current_r / 100.0
                lifetime_divergence += (product_shadow - product_current) * 100.0
            dry_run = if_held + lifetime_divergence

    return {"if_held": if_held, "dry_run": dry_run, "_twr_fallback": _twr_fallback}


def get_symphony_max_drawdown(
    sym_dict: dict,
    bot_state_entry: dict | None,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """
    Per-symphony Max Drawdown.

    if_held: max_drawdown from Composer (positive float, magnitude convention).
    dry_run: peak-to-trough drawdown of the BOT's equity path (AC-M1F.3.3). The bot
             equity at each EOD step t is the held baseline plus the cumulative
             guard-alpha divergence up to that step:
                 bot_equity[t] = if_held + (prod_shadow[0..t] - prod_current[0..t]) * 100
             This is the divergence-based equity series, NOT the absolute shadow
             cumulative — so for an untriggered symphony (shadow == current every day)
             the bot equity is flat at if_held and MDD is exactly 0 (no phantom
             drawdown). Once the guard triggers, the shadow series freezes while the
             held (current) series keeps moving, and the divergence opens a real
             drawdown that this peak-to-trough captures.
             Falls back to if_held when no shadow trajectory exists and trading_day was
             not explicitly provided — preserves pre-M1F semantics for old callers.
             None when shadow_history is explicitly queried (trading_day present) but empty
             (AC-M1F.3.5).

    Returns {"if_held": None, "dry_run": None} when max_drawdown is None (missing data).
    """
    if sym_dict.get("max_drawdown") is None:
        return {"if_held": None, "dry_run": None}
    if_held = float(sym_dict["max_drawdown"]) * 100.0

    symphony_id = sym_dict.get("id")

    dry_run: float | None = None
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        trajectory = _get_shadow_divergence_trajectory(symphony_id, _db_file)
        if trajectory is not None:
            # Build the bot's EPOCH-ADDITIVE divergence equity series, then
            # peak-to-trough (semantic B). Divergence accrues within each epoch from
            # its OWN anchor (products reset per epoch); the running realized alpha
            # carries across epoch boundaries as the next epoch's starting level, so a
            # prior epoch's locked-in guard effect is preserved WITHOUT chaining its
            # market returns into the later epoch's products. Untriggered epochs add a
            # flat segment at the running level (zero intra-epoch divergence).
            bot_equity: list[float] = []
            running_alpha = 0.0
            for epoch_pairs in trajectory:
                product_shadow = 1.0
                product_current = 1.0
                epoch_start_alpha = running_alpha
                for shadow_r, current_r in epoch_pairs:
                    product_shadow *= 1.0 + shadow_r / 100.0
                    product_current *= 1.0 + current_r / 100.0
                    bot_equity.append(
                        if_held + epoch_start_alpha + (product_shadow - product_current) * 100.0
                    )
                running_alpha = epoch_start_alpha + (product_shadow - product_current) * 100.0
            if len(bot_equity) >= 2:
                peak = bot_equity[0]
                max_dd = 0.0
                for val in bot_equity:
                    if val > peak:
                        peak = val
                    dd = peak - val
                    if dd > max_dd:
                        max_dd = dd
                dry_run = max_dd

    return {"if_held": if_held, "dry_run": dry_run}


def _value_weighted_portfolio(
    symphonies: list[dict],
    bot_state: dict,
    per_sym_fn,
    *,
    none_on_empty: bool = False,
    **kwargs,
) -> dict:
    """
    Value-weighted aggregate of a per-symphony helper across all symphonies.

    Symphonies missing "value", with value <= 0, or whose per_sym_fn returns
    if_held=None (missing-data sentinel) are skipped for both if_held and dry_run.
    dry_run=None contributors are excluded from the dry_run aggregate independently
    (AC-M1F.3.6 — consistent with existing if_held=None skip behavior).
    When none_on_empty=True: returns {"if_held": None, "dry_run": None} when
    symphonies is empty, all weights are non-positive, or all symphonies have
    missing data — used by CR and MDD where 0.0 is ambiguous with real zero.
    When none_on_empty=False (default): returns {"if_held": 0.0, "dry_run": 0.0}
    — used by TC where 0.0 is semantically correct for no-data.
    """
    total_weight = 0.0
    if_held_wsum = 0.0
    dry_run_wsum = 0.0
    dry_run_weight = 0.0

    for sym in symphonies:
        w = sym.get("value")
        if w is None:
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(w) and w > 0.0):
            continue

        entry = bot_state.get(sym.get("id"))
        per = per_sym_fn(sym, entry, **kwargs)
        if per["if_held"] is None:
            continue
        # F4 fix: exclude TWR-fallback symphonies (zero-deposit, simple_return==0)
        # from the portfolio VW aggregate. Their 300%+ TWR figures are defensible
        # per-card but constitute outlier noise at the portfolio level. The flag
        # "_twr_fallback" is set by get_symphony_cumulative_return; other per_sym_fn
        # implementations don't set it, so this check is specific to the CR path.
        if per.get("_twr_fallback"):
            continue
        if_held_wsum += per["if_held"] * w
        total_weight += w
        # AC-M1F.3.6: exclude None dry_run contributors independently
        if per["dry_run"] is not None:
            dry_run_wsum += per["dry_run"] * w
            dry_run_weight += w

    if total_weight == 0.0:
        if none_on_empty:
            return {"if_held": None, "dry_run": None}
        return {"if_held": 0.0, "dry_run": 0.0}

    dry_run_result: float | None = dry_run_wsum / dry_run_weight if dry_run_weight > 0.0 else None
    return {
        "if_held": if_held_wsum / total_weight,
        "dry_run": dry_run_result,
    }


def get_portfolio_today_change(
    symphonies: list[dict],
    bot_state: dict,
    *,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Value-weighted portfolio Today's Change across all symphonies."""
    return _value_weighted_portfolio(
        symphonies,
        bot_state,
        get_symphony_today_change,
        trading_day=trading_day,
        db_path=db_path,
    )


def get_portfolio_cumulative_return(
    symphonies: list[dict],
    bot_state: dict,
    *,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Value-weighted portfolio Cumulative Return across all symphonies."""
    return _value_weighted_portfolio(
        symphonies,
        bot_state,
        get_symphony_cumulative_return,
        none_on_empty=True,
        trading_day=trading_day,
        db_path=db_path,
    )


def get_portfolio_max_drawdown(
    symphonies: list[dict],
    bot_state: dict,
    *,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Value-weighted portfolio Max Drawdown across all symphonies."""
    return _value_weighted_portfolio(
        symphonies,
        bot_state,
        get_symphony_max_drawdown,
        none_on_empty=True,
        trading_day=trading_day,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# account-basis portfolio CR  (B-1 fix)
# ---------------------------------------------------------------------------


def get_portfolio_cumulative_return_account_basis(
    vw_cr: dict,
    account_if_held: float,
    account_value: float,
    symphony_value_sum: float,
) -> dict:
    """Re-express portfolio Cumulative Return on an account (cash-inclusive) basis.

    The VW portfolio CR is computed over invested capital only (cash excluded).
    The Composer "Held" figure (simple_return) uses account value as the denominator
    (cash-inclusive).  Subtracting the two is a scope artefact, not guard alpha.

    This function translates the bot's guard-alpha DIVERGENCE from VW symphony-basis
    to account-basis so that Bot and Held share a common denominator:

        guard_delta_vw = vw_cr["dry_run"] - vw_cr["if_held"]   # pure guard effect, VW basis
        invested_frac  = symphony_value_sum / account_value     # fraction of account deployed
        dry_run_account = account_if_held + guard_delta_vw * invested_frac

    The guard effect is measured against the VW if_held (same denominator as dry_run),
    then scaled by invested_frac so it is expressed as a fraction of account value,
    then applied to the account-level Held return.

    Args:
        vw_cr:               {"if_held": float, "dry_run": float | None} — VW portfolio CR
                             Both fields use symphony value-sum as denominator.
        account_if_held:     Composer simple_return * 100 (account-level Held, cash-inclusive)
        account_value:       total account value including cash (from _account_totals_cache)
        symphony_value_sum:  sum of invested symphony values (cash excluded)

    Returns:
        {"if_held": account_if_held, "dry_run": account-basis dry_run | None}

    Guard invariants:
        - Untriggered symphonies have dry_run == if_held on VW basis → guard_delta_vw == 0 →
          dry_run_account == account_if_held (no phantom alpha regardless of cash).
        - if account_value <= 0 or symphony_value_sum <= 0: returns vw_cr unchanged
          (division guard; caller should treat as a missing-data case).
        - if vw_cr["dry_run"] is None or vw_cr["if_held"] is None: returns
          {"if_held": account_if_held, "dry_run": None}.
    """
    if not (math.isfinite(account_value) and account_value > 0.0):
        return vw_cr
    if not (math.isfinite(symphony_value_sum) and symphony_value_sum > 0.0):
        return vw_cr
    if vw_cr.get("if_held") is None:
        return {"if_held": account_if_held, "dry_run": None}
    if vw_cr.get("dry_run") is None:
        return {"if_held": account_if_held, "dry_run": None}

    invested_frac = symphony_value_sum / account_value
    # Guard delta measured on VW basis (dry_run and if_held share the same
    # symphony-value denominator, so this is a clean pure-guard-effect measure).
    guard_delta_vw = float(vw_cr["dry_run"]) - float(vw_cr["if_held"])
    # Scale to account basis and apply to the account-level Held return.
    dry_run_account = account_if_held + guard_delta_vw * invested_frac
    return {"if_held": account_if_held, "dry_run": dry_run_account}


def get_portfolio_today_change_account_basis(
    vw_tc: dict,
    account_if_held_tc: float,
    account_value: float,
    symphony_value_sum: float,
) -> dict:
    """Re-express portfolio Today's Change on an account (cash-inclusive) basis.

    The VW portfolio TC is computed over invested capital only (cash excluded).
    The Composer "Held" figure (todays_percent_change) uses account value as the
    denominator (cash-inclusive).  Subtracting the two is a scope artefact, not
    guard alpha.

    This function translates the bot's guard-alpha DIVERGENCE from VW symphony-basis
    to account-basis so that Bot and Held share a common denominator:

        guard_delta_vw = vw_tc["dry_run"] - vw_tc["if_held"]   # pure guard effect, VW basis
        invested_frac  = symphony_value_sum / account_value     # fraction of account deployed
        dry_run_account = account_if_held_tc + guard_delta_vw * invested_frac

    The guard effect is measured against the VW if_held (same denominator as dry_run),
    then scaled by invested_frac so it is expressed as a fraction of account value,
    then applied to the account-level Held today-change.

    Args:
        vw_tc:               {"if_held": float, "dry_run": float | None} — VW portfolio TC
                             Both fields use symphony value-sum as denominator.
        account_if_held_tc:  Composer todays_percent_change * 100 (account-level Held,
                             cash-inclusive)
        account_value:       total account value including cash (from _account_totals_cache)
        symphony_value_sum:  sum of invested symphony values (cash excluded)

    Returns:
        {"if_held": account_if_held_tc, "dry_run": account-basis dry_run | None}

    Guard invariants:
        - Untriggered symphonies have dry_run == if_held on VW basis → guard_delta_vw == 0 →
          dry_run_account == account_if_held_tc (no phantom alpha regardless of cash).
        - if account_value <= 0/non-finite or symphony_value_sum <= 0/non-finite: returns
          {"if_held": account_if_held_tc, "dry_run": account_if_held_tc} (Bot==Held;
          no phantom alpha — invested_frac is undefined so guard effect can't be scaled).
        - if account_if_held_tc is None: returns {"if_held": None, "dry_run": None}
          (account-basis result is undefined; propagate None cleanly).
        - if vw_tc["dry_run"] is None or vw_tc["if_held"] is None: returns
          {"if_held": account_if_held_tc, "dry_run": None}.
        - invested_frac is clamped to min(..., 1.0): symphony_value_sum > account_value
          is inconsistent (cash non-negative) but a stale snapshot can produce it;
          clamping prevents guard-delta amplification beyond the VW magnitude.
    """
    if not (math.isfinite(account_value) and account_value > 0.0):
        # Division guard: can't compute invested_frac; no measurable guard effect →
        # Bot == Held on account basis (conservative, no phantom alpha).
        return {"if_held": account_if_held_tc, "dry_run": account_if_held_tc}
    if not (math.isfinite(symphony_value_sum) and symphony_value_sum > 0.0):
        # Division guard: zero/missing deployed capital → invested_frac undefined →
        # Bot == Held on account basis (conservative, no phantom alpha).
        return {"if_held": account_if_held_tc, "dry_run": account_if_held_tc}
    if account_if_held_tc is None:
        # Account-level Held unavailable; account-basis result undefined.
        return {"if_held": None, "dry_run": None}
    if vw_tc.get("if_held") is None:
        return {"if_held": account_if_held_tc, "dry_run": None}
    if vw_tc.get("dry_run") is None:
        return {"if_held": account_if_held_tc, "dry_run": None}

    # Cap at 1.0: symphony_value_sum > account_value is inconsistent (cash can't be
    # negative) but a stale snapshot could produce it; clamping prevents amplification
    # of the guard delta beyond its VW-basis magnitude (operational policy).
    invested_frac = min(symphony_value_sum / account_value, 1.0)
    # Guard delta measured on VW basis (dry_run and if_held share the same
    # symphony-value denominator, so this is a clean pure-guard-effect measure).
    guard_delta_vw = float(vw_tc["dry_run"]) - float(vw_tc["if_held"])
    # Scale to account basis and apply to the account-level Held today-change.
    dry_run_account = account_if_held_tc + guard_delta_vw * invested_frac
    return {"if_held": account_if_held_tc, "dry_run": dry_run_account}


# ---------------------------------------------------------------------------
# get_history_with_cache_invalidation
# ---------------------------------------------------------------------------


def get_history_with_cache_invalidation(days: int = 60, base_dir: str = ".") -> dict:
    """
    Cached wrapper around load_post_mortem_history.

    Cache key: (max(post_mortem_*.json mtime in base_dir), days, base_dir).
    On any change to the latest-mtime, days, or base_dir, the underlying
    loader is re-invoked. Otherwise the previously-loaded dict is returned.

    Rationale: the dashboard polls this on every refresh; an O(N-file)
    scan + JSON parse every render is wasted work, but we must still pick
    up the next-day post-mortem the moment it lands.
    """
    global _HISTORY_CACHE

    pattern = os.path.join(base_dir, _POST_MORTEM_GLOB)
    latest_mtime = 0.0
    for fpath in glob.glob(pattern):
        try:
            m = os.path.getmtime(fpath)
        except OSError:
            continue
        if m > latest_mtime:
            latest_mtime = m

    cache_key = (latest_mtime, days, base_dir)
    cached = _HISTORY_CACHE.get("key") if isinstance(_HISTORY_CACHE, dict) else None
    if cached == cache_key and _HISTORY_CACHE.get("data") is not None:
        return _HISTORY_CACHE["data"]

    data = load_post_mortem_history(days=days, base_dir=base_dir)
    _HISTORY_CACHE = {"key": cache_key, "data": data}
    return data


def _load_position_value_weights() -> dict[str, float]:
    """Per-symphony position values from bot_state ``current_value`` — the genuine
    value-weight source for the canonical portfolio series (audit Finding 6: the
    prior abs(current_return) "value-weight proxy" let the day's biggest movers
    dominate, exaggerating portfolio levels ~4x).

    Only positive finite values participate. An empty dict means "no position
    values available" (day-1 droplet) and the caller degrades to EQUAL weight —
    never back to the abs-return proxy.
    """
    try:
        state = database.load_state()
    except Exception:
        return {}
    weights: dict[str, float] = {}
    for sym_id, entry in (state or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            value = float(entry.get("current_value"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            weights[sym_id] = value
    return weights


def get_portfolio_daily_returns_from_shadow(
    db_file: str | None = None,
    days: int = 125,
) -> tuple[list[str], list[float]] | None:
    """Build a continuous portfolio-level daily return series from shadow_history.

    Aggregates per-symphony shadow_return values for each trading day using the
    last row per (symphony_id, trading_day). Value-weights by bot_state
    ``current_value`` (equal-weight fallback when no position values exist).
    Returns (dates_sorted_ascending, portfolio_daily_returns) or None when fewer
    than 2 distinct trading days exist — callers should fall back to post_mortem
    data.

    This is the correct source for the hero chart hist series: it is continuous
    (written every cycle for every tracked symphony, not only on exit days), so its
    compounded terminal value converges to the real portfolio return over time.
    """
    import sqlite3

    _db_file = db_file if db_file is not None else _get_shadow_db_file()
    try:
        conn = sqlite3.connect(f"file:{_db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute(
            "SELECT trading_day, symphony_id, shadow_return, current_return "
            "FROM shadow_history "
            "WHERE ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = shadow_history.symphony_id "
            "                  AND s2.trading_day = shadow_history.trading_day) "
            "ORDER BY trading_day ASC, symphony_id ASC",
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    # Group by trading_day → {symphony_id: (shadow_return, current_return)}
    from collections import defaultdict

    day_map: dict = defaultdict(dict)
    for trading_day, symphony_id, shadow_return, current_return in rows:
        day_map[trading_day][symphony_id] = (
            float(shadow_return) if shadow_return is not None else 0.0,
            float(current_return) if current_return is not None else 0.0,
        )

    # Only keep the most recent `days` trading days.
    sorted_days = sorted(day_map.keys())[-days:]
    if len(sorted_days) < 2:
        return None

    value_weights = _load_position_value_weights()

    out_dates: list[str] = []
    out_returns: list[float] = []
    for day in sorted_days:
        entries = day_map[day]
        weight_sum = 0.0
        ret_wsum = 0.0
        for sym_id, (sym_ret, _sym_cr) in entries.items():
            # Genuine value-weight from bot_state current_value; EQUAL weight when
            # no position values exist — never the abs(return) proxy (Finding 6).
            w = value_weights.get(sym_id, 0.0) if value_weights else 1.0
            if w <= 0.0:
                continue
            weight_sum += w
            ret_wsum += sym_ret * w
        if weight_sum > 0.0:
            out_dates.append(day)
            out_returns.append(ret_wsum / weight_sum)

    if len(out_dates) < 2:
        return None

    return out_dates, out_returns


def get_portfolio_bot_and_held_daily_returns(
    db_file: str | None = None,
    days: int | None = 125,
) -> tuple[list[str], list[float], list[float]] | None:
    """Build BOTH the portfolio Bot and Held continuous daily-return series from
    shadow_history — the source for the hero chart's two distinct lines (AC-4b/F2-F3).

    Sibling of ``get_portfolio_daily_returns_from_shadow`` (which emits the Bot series
    only). The audit (F2/F3) found ``/api/hero-chart`` returned ``hist_held =
    bot_series`` (the same object), so the dashed "If held" line traced Bot exactly —
    zero visual guard alpha by construction. This function returns a GENUINE held
    series distinct from bot wherever the guard diverged.

    Per day, using the last row per (symphony_id, trading_day):
      - Bot  = value-weighted ``shadow_return``  (the guard's series — frozen at exit)
      - Held = value-weighted ``current_return`` (the if-held baseline — kept holding)
    BOTH series use the SAME per-symphony weight (bot_state ``current_value``;
    equal-weight fallback when no position values exist) so Bot and Held are
    commensurable and their difference is a real guard effect, not a
    re-weighting artefact. For an
    untriggered symphony shadow_return == current_return, so its Bot and Held
    contributions coincide; an all-untriggered day yields bot == held EXACTLY (no
    fabricated divergence).

    NOT epoch-scoped: this is the CONTINUOUS portfolio series (every cycle, every
    tracked symphony), the correct hist source — distinct from the per-symphony
    epoch-additive guard-alpha trajectory (which IS epoch-grouped). The hero chart
    compounds each returned series independently for its two lines.

    Args:
        db_file: shadow DB path override (tests); defaults to the live shadow DB.
        days:    cap on the most recent trading days to include. ``None`` = all
                 history (the "All Time" window).

    Returns:
        (dates_ascending, bot_daily_returns_pct, held_daily_returns_pct), or None
        when fewer than 2 distinct trading days exist (caller falls back).
    """
    import sqlite3

    _db_file = db_file if db_file is not None else _get_shadow_db_file()
    try:
        conn = sqlite3.connect(f"file:{_db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute(
            "SELECT trading_day, symphony_id, shadow_return, current_return "
            "FROM shadow_history "
            "WHERE ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = shadow_history.symphony_id "
            "                  AND s2.trading_day = shadow_history.trading_day) "
            "ORDER BY trading_day ASC, symphony_id ASC",
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    # Group by trading_day → {symphony_id: (shadow_return, current_return)}
    from collections import defaultdict

    day_map: dict = defaultdict(dict)
    for trading_day, symphony_id, shadow_return, current_return in rows:
        day_map[trading_day][symphony_id] = (
            float(shadow_return) if shadow_return is not None else 0.0,
            float(current_return) if current_return is not None else 0.0,
        )

    # Keep the most recent `days` trading days (all history when days is None).
    all_days = sorted(day_map.keys())
    sorted_days = all_days if days is None else all_days[-days:]
    if len(sorted_days) < 2:
        return None

    value_weights = _load_position_value_weights()

    out_dates: list[str] = []
    bot_returns: list[float] = []
    held_returns: list[float] = []
    for day in sorted_days:
        entries = day_map[day]
        weight_sum = 0.0
        bot_wsum = 0.0
        held_wsum = 0.0
        for sym_id, (sym_ret, sym_cr) in entries.items():
            # ONE weight per symphony (bot_state current_value; equal-weight when
            # no position values exist — never the abs(return) proxy, Finding 6),
            # applied to BOTH series so Bot and Held stay commensurable.
            w = value_weights.get(sym_id, 0.0) if value_weights else 1.0
            if w <= 0.0:
                continue
            weight_sum += w
            bot_wsum += sym_ret * w
            held_wsum += sym_cr * w
        if weight_sum > 0.0:
            out_dates.append(day)
            bot_returns.append(bot_wsum / weight_sum)
            held_returns.append(held_wsum / weight_sum)

    if len(out_dates) < 2:
        return None

    return out_dates, bot_returns, held_returns


def get_symphony_bot_and_held_daily_returns(
    symphony_id: str,
    db_file: str | None = None,
    days: int | None = 125,
) -> tuple[list[str], list[float], list[float]] | None:
    """Per-symphony analogue of ``get_portfolio_bot_and_held_daily_returns`` — the
    canonical CONTINUOUS shadow_history series for ONE symphony (AC-3 / MA-6 /
    MAPERF-01).

    Fix direction: ``/api/performance?scope=symphony`` was sourcing its series from
    ``compute_per_symphony_returns`` over post-mortem trigger arrays — a selection-
    biased event sample containing ONLY the days the symphony triggered (every
    zero-trigger day silently absent). ``compute_quantstats_metrics`` then placed
    those K trigger-day observations on a synthetic CONSECUTIVE daily index and
    annualized as if they were K consecutive trading days (a 4-trigger sample
    averaging ~0.45%/day annualizing to ~209.8% CAGR). This function instead reads
    the last row per (symphony_id, trading_day) from shadow_history directly — the
    same source class the aggregate scope uses (Finding 4) — so every trading day
    the symphony has a shadow_history row appears, triggered or not.

    NOT epoch-scoped: like its portfolio sibling, this is the continuous per-day
    series for risk-metric computation, distinct from the epoch-additive guard-alpha
    trajectory (``compute_windowed_symphony_guard_alpha`` et al.) used by the
    History tab / windowed strip, which remains untouched.

    Per day: Bot = ``shadow_return``, Held = ``current_return`` (no weighting needed
    — a single symphony contributes to itself).

    Args:
        symphony_id: the symphony to read.
        db_file: shadow DB path override (tests); defaults to the live shadow DB.
        days:    cap on the most recent trading days to include. ``None`` = all
                 history.

    Returns:
        (dates_ascending, bot_daily_returns_pct, held_daily_returns_pct), or None
        when fewer than 2 distinct trading days exist for this symphony (mirrors
        the aggregate function's floor — the route's own honest-empty-state path
        takes over below that).
    """
    import sqlite3

    _db_file = db_file if db_file is not None else _get_shadow_db_file()
    try:
        conn = sqlite3.connect(f"file:{_db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute(
            "SELECT trading_day, shadow_return, current_return "
            "FROM shadow_history "
            "WHERE symphony_id = ? "
            "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = shadow_history.symphony_id "
            "                  AND s2.trading_day = shadow_history.trading_day) "
            "ORDER BY trading_day ASC",
            (symphony_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    all_dates = [r[0] for r in rows]
    all_bot = [float(r[1]) if r[1] is not None else 0.0 for r in rows]
    all_held = [float(r[2]) if r[2] is not None else 0.0 for r in rows]

    # Keep the most recent `days` trading days (all history when days is None).
    if days is None:
        out_dates, bot_returns, held_returns = all_dates, all_bot, all_held
    else:
        out_dates = all_dates[-days:]
        bot_returns = all_bot[-days:]
        held_returns = all_held[-days:]

    if len(out_dates) < 2:
        return None

    return out_dates, bot_returns, held_returns


def get_single_day_shadow_returns(
    db_file: str | None = None,
) -> tuple[list[str], list[float], list[float]] | None:
    """Return a single-day (dates, bot_returns, held_returns) tuple from shadow_history.

    AC-2b: get_portfolio_bot_and_held_daily_returns() returns None when fewer than
    2 distinct trading days exist.  On a fresh droplet (day one), that guard fires
    and the performance route returned observation_count=0.  This function provides
    a minimal 1-entry fallback — one date, one bot return, one held return — so the
    chart is non-empty from day one even before the multi-day guard can pass.

    Returns (dates, bot_daily_returns_pct, held_daily_returns_pct) where each list
    has exactly 1 element, or None when shadow_history is empty or unreadable.
    """
    import sqlite3

    _db_file = db_file if db_file is not None else _get_shadow_db_file()
    try:
        conn = sqlite3.connect(f"file:{_db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute(
            "SELECT trading_day, symphony_id, shadow_return, current_return "
            "FROM shadow_history "
            "WHERE ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = shadow_history.symphony_id "
            "                  AND s2.trading_day = shadow_history.trading_day) "
            "ORDER BY trading_day DESC, shadow_history.symphony_id ASC",
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    # Use the latest trading_day only; same weight policy as the multi-day series
    # (bot_state current_value, equal-weight fallback — never the abs proxy).
    latest_day = rows[0][0]
    day_rows = [r for r in rows if r[0] == latest_day]
    value_weights = _load_position_value_weights()

    weight_sum = 0.0
    bot_wsum = 0.0
    held_wsum = 0.0
    for _day, sym_id, shadow_ret, cur_ret in day_rows:
        shadow_f = float(shadow_ret) if shadow_ret is not None else 0.0
        cur_f = float(cur_ret) if cur_ret is not None else 0.0
        w = value_weights.get(sym_id, 0.0) if value_weights else 1.0
        if w <= 0.0:
            continue
        weight_sum += w
        bot_wsum += shadow_f * w
        held_wsum += cur_f * w

    if weight_sum == 0.0:
        return None

    return [latest_day], [bot_wsum / weight_sum], [held_wsum / weight_sum]


# V1 bootstrap gate — three-state fold-sufficiency check (PA-M1F-11, AC-M1F.6.4)
# Threshold: N >= 30 per Bailey/de-Prado 2014 interpretability floor.
_V1_BOOTSTRAP_MIN_DAYS = 30

# AC-3 windowed-metric tokens. Numeric tokens are a trailing-day count; ytd = since
# Jan 1 of the current year; 1y = trailing 365 calendar days; all = no window (lifetime).
_WINDOW_TRAILING_DAYS = {"30d": 30, "60d": 60, "90d": 90, "125d": 125, "1y": 365}
# Reuse the Bailey/de-Prado interpretability floor as the windowed vol sufficiency
# gate (F7): vol is meaningless on a handful of days, so it is suppressed below this.
_WINDOWED_VOL_MIN_DAYS = _V1_BOOTSTRAP_MIN_DAYS


def _window_cutoff_date(window: object) -> date | None:
    """Resolve a window token to a trading-day CUTOFF (inclusive lower bound), or None
    for the lifetime ("all") window. Date-based (not positional) so a window excludes
    rows older than its span regardless of how many rows exist (W1 slice-then-regroup).

    Accepts the lowercase route tokens 30d/60d/90d/125d/ytd/1y/all (case-insensitive)
    and a bare int day-count (trailing days). Unknown tokens resolve to None (lifetime)
    — the route validates tokens against its allowlist before calling, so this is a
    permissive fallback, not the gate.
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    today = _dt.now(UTC).date()
    if isinstance(window, int):
        return today - _td(days=window)
    token = str(window).lower()
    if token == "all":
        return None
    if token == "ytd":
        return date(today.year, 1, 1)
    n = _WINDOW_TRAILING_DAYS.get(token)
    if n is None:
        return None  # unknown token -> lifetime (route already allowlisted)
    return today - _td(days=n)


def _get_windowed_divergence_trajectory(
    symphony_id: str, db_file: str, window: object
) -> list[list[tuple[float, float]]] | None:
    """Like ``_get_shadow_divergence_trajectory`` (lifetime, epoch-grouped) but filtered
    to the rows whose trading_day falls within ``window`` (W1 slice-then-regroup).

    Selects the last EOD row per (symphony_id, trading_day) across ALL epochs, keeps
    only trading_days >= the window cutoff, then re-groups the SLICED rows into
    contiguous position-epoch runs. window="all" applies no cutoff so the result is
    identical to the lifetime trajectory (the consistency anchor with AC-1).

    Returns None when fewer than 2 in-window trading days exist.
    """
    import sqlite3

    cutoff = _window_cutoff_date(window)
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute(
            "SELECT trading_day, shadow_return, current_return, position_epoch "
            "FROM shadow_history sh "
            "WHERE symphony_id = ? "
            "  AND ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = sh.symphony_id "
            "                  AND s2.trading_day = sh.trading_day) "
            "ORDER BY trading_day ASC",
            (symphony_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return None

    cutoff_iso = cutoff.isoformat() if cutoff is not None else None
    # String compare is valid: trading_day is ISO "YYYY-MM-DD" (lexicographic == chronological).
    in_window = [r for r in rows if cutoff_iso is None or str(r[0]) >= cutoff_iso]
    if len(in_window) < 2:
        return None

    groups: list[list[tuple[float, float]]] = []
    _sentinel = object()
    current_label: object = _sentinel
    for _trading_day, shadow_r, current_r, epoch_label in in_window:
        if epoch_label != current_label:
            groups.append([])
            current_label = epoch_label
        groups[-1].append((float(shadow_r), float(current_r)))
    return groups


def _epoch_additive_divergence(groups: list[list[tuple[float, float]]]) -> float:
    """Epoch-additive guard-alpha divergence (semantic B): SUM over epoch groups of
    each group's own ``(∏(1+shadow/100) − ∏(1+current/100)) * 100``. Identical to the
    AC-1 lifetime computation; shared so windowed and lifetime agree by construction."""
    total = 0.0
    for epoch_pairs in groups:
        product_shadow = 1.0
        product_current = 1.0
        for shadow_r, current_r in epoch_pairs:
            product_shadow *= 1.0 + shadow_r / 100.0
            product_current *= 1.0 + current_r / 100.0
        total += (product_shadow - product_current) * 100.0
    return total


def compute_windowed_symphony_guard_alpha(
    sym_dict: dict,
    bot_state_entry: dict | None,
    *,
    window: object,
    db_path: str | None = None,
) -> float | None:
    """Per-symphony guard alpha (dry_run − if_held) over a SELECTABLE window (AC-3).

    The guard alpha IS the epoch-additive divergence (dry_run − if_held cancels the
    if_held baseline), so this is the window-sliced, epoch-regrouped, epoch-additive
    sum. window="all" reproduces the AC-1 lifetime guard alpha EXACTLY. Untriggered
    symphonies (shadow == current) yield a genuinely-computed 0.0 on every window.
    Returns None when the symphony has no id (cannot read shadow history) OR when
    the window has < 2 days of recorded divergence — the deliberate conservatism
    floor in ``_get_windowed_divergence_trajectory`` (AC-8b: this is an "unknown,
    insufficient data" state, not a computed zero; the floor itself is unchanged,
    only its return encoding — collapsing both cases into a fabricated 0.0 made
    the two indistinguishable to callers, which silently withheld the day-1
    intraday fallback even when a thin window held a REAL divergence).
    """
    symphony_id = sym_dict.get("id")
    if not symphony_id:
        return None
    _db_file = db_path if db_path is not None else _get_shadow_db_file()
    trajectory = _get_windowed_divergence_trajectory(symphony_id, _db_file, window)
    if trajectory is None:
        return None
    return _epoch_additive_divergence(trajectory)


def compute_windowed_portfolio_strip(
    symphonies: list[dict],
    bot_state: dict,
    *,
    window: object,
    db_path: str | None = None,
) -> dict:
    """Recompute the hero comparison strip FOR a selectable window (AC-3).

    Returns a dict the dashboard renders per the picker, echoing the resolved window
    so the label always matches the value (kills the F1 "30d"-label-on-all-time-value
    dishonesty):

        {
          "today_change":      {"dry_run", "if_held"},   # window-independent (today only)
          "cumulative_return": {"dry_run", "if_held"},   # dry_run = if_held + windowed alpha
          "max_drawdown":      {"dry_run", "if_held"},
          "vol_bot", "vol_held":   annualized vol of the windowed portfolio series, or None,
          "guard_alpha":           windowed portfolio guard alpha (VW of per-symphony),
          "insufficient_history":  True when the window has < _WINDOWED_VOL_MIN_DAYS days,
          "window":                the echoed resolved window token,
        }

    Bot and Held are BOTH on the VW (cash-excluded) basis within a window — there is no
    daily account-level held series from Composer, so windowed views are internally
    commensurable; the account-basis rescale applies only to the all-time hero scalar.

    F7 vol gate: vol_bot/vol_held are None and insufficient_history is True when the
    window's trading-day count is below _WINDOWED_VOL_MIN_DAYS (Bailey/de-Prado floor).
    """
    _db_file = db_path if db_path is not None else _get_shadow_db_file()

    # CR / MDD / TC reuse the existing per-symphony helpers via VW aggregation. The
    # windowed guard alpha is added to the (window-independent) if_held baseline so the
    # picker re-windows only the guard EFFECT, never the Composer lifetime anchor.
    cr = get_portfolio_cumulative_return(symphonies, bot_state, db_path=_db_file)
    mdd = get_portfolio_max_drawdown(symphonies, bot_state, db_path=_db_file)
    # today_change is window-independent (today only). It needs last_percent_change,
    # which the live route supplies but a minimal caller may omit; degrade to a null
    # strip entry rather than failing the whole windowed strip.
    try:
        tc = get_portfolio_today_change(symphonies, bot_state, db_path=_db_file)
    except (KeyError, TypeError, ValueError):
        tc = {"dry_run": None, "if_held": None}

    # Windowed portfolio guard alpha = value-weighted per-symphony windowed alpha,
    # skipping symphonies with non-positive/missing value (same rule as the VW helper).
    weight_sum = 0.0
    alpha_wsum = 0.0
    for sym in symphonies:
        w = sym.get("value")
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(w) and w > 0.0):
            continue
        entry = bot_state.get(sym.get("id"))
        sym_alpha = compute_windowed_symphony_guard_alpha(
            sym, entry, window=window, db_path=_db_file
        )
        if sym_alpha is None:
            continue
        alpha_wsum += sym_alpha * w
        weight_sum += w
    windowed_alpha: float | None = alpha_wsum / weight_sum if weight_sum > 0.0 else None

    # Anchor the windowed CR dry_run on the windowed guard alpha so the headline value
    # re-windows (cumulative_return.dry_run − if_held == windowed guard alpha).
    cr_if_held = cr.get("if_held")
    cr_out = dict(cr)
    if cr_if_held is not None and windowed_alpha is not None:
        cr_out = {"if_held": cr_if_held, "dry_run": cr_if_held + windowed_alpha}

    # Windowed portfolio daily series -> annualized vol, gated by the day-count floor.
    series = get_portfolio_bot_and_held_daily_returns(_db_file, days=None)
    vol_bot: float | None = None
    vol_held: float | None = None
    window_day_count = 0
    if series is not None:
        all_dates, bot_pct, held_pct = series
        cutoff = _window_cutoff_date(window)
        cutoff_iso = cutoff.isoformat() if cutoff is not None else None
        idx = [i for i, d in enumerate(all_dates) if cutoff_iso is None or str(d) >= cutoff_iso]
        window_day_count = len(idx)
        if window_day_count >= _WINDOWED_VOL_MIN_DAYS:
            vol_bot = compute_portfolio_annualized_vol([bot_pct[i] for i in idx])
            vol_held = compute_portfolio_annualized_vol([held_pct[i] for i in idx])

    insufficient_history = window_day_count < _WINDOWED_VOL_MIN_DAYS

    return {
        "today_change": tc,
        "cumulative_return": cr_out,
        "max_drawdown": mdd,
        "vol_bot": vol_bot,
        "vol_held": vol_held,
        "guard_alpha": windowed_alpha,
        "insufficient_history": insufficient_history,
        "window": str(window).lower(),
    }


def get_history_summary(days: int = 30, base_dir: str = ".") -> dict:
    """Aggregate guard-alpha history for the History tab.

    Returns the same envelope the /api/history/<days> route emits, derived
    from post_mortem_*.json files in base_dir. Exists so the route can delegate
    here and tests can mock this single function.
    """
    import glob as _glob
    import json as _json
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    end_date = _dt.now()
    start_date = end_date - _td(days=days)
    files = _glob.glob(os.path.join(base_dir, "post_mortem_*.json"))

    stats: dict = {
        "total_alpha": 0.0,
        "total_saved": 0.0,
        "trigger_count": 0,
        "wins": 0,
        "by_reason": {},
    }
    daily_map: dict = {}

    for f_path in files:
        try:
            date_part = os.path.basename(f_path).replace("post_mortem_", "").replace(".json", "")
            file_date = _dt.strptime(date_part, "%Y-%m-%d")
            if not (start_date <= file_date <= end_date):
                continue
            with open(f_path, encoding="utf-8") as fh:
                data = _json.load(fh)
            day_alpha = 0.0
            for t in data.get("triggers", []):
                alpha = t.get("saved_pct_guard_alpha", 0.0)
                dollars = t.get("saved_dollars", 0.0)
                reason = t.get("exit_reason", "Unknown")
                stats["total_alpha"] += alpha
                stats["total_saved"] += dollars
                stats["trigger_count"] += 1
                day_alpha += alpha
                if alpha > 0:
                    stats["wins"] += 1
                br = stats["by_reason"].setdefault(
                    reason, {"alpha": 0.0, "count": 0, "wins": 0, "dollars": 0.0}
                )
                br["alpha"] += alpha
                br["count"] += 1
                br["dollars"] += dollars
                if alpha > 0:
                    br["wins"] += 1
            daily_map[date_part] = daily_map.get(date_part, 0.0) + day_alpha
        except Exception:
            continue

    if stats["trigger_count"] > 0:
        stats["avg_guard_alpha"] = stats["total_alpha"] / stats["trigger_count"]
        stats["win_rate"] = (stats["wins"] / stats["trigger_count"]) * 100
    else:
        stats["avg_guard_alpha"] = 0.0
        stats["win_rate"] = 0.0

    stats["daily_alpha"] = [daily_map[d] for d in sorted(daily_map)]

    today_str = _date.today().isoformat()
    today_file = os.path.join(base_dir, "post_mortem_" + today_str + ".json")
    todays_exits: list = []
    try:
        with open(today_file, encoding="utf-8") as fh:
            today_data = _json.load(fh)

        # Build symphony_id -> name lookup from live state (FP-T1-07).
        _name_map: dict = {}
        try:
            _live_state = database.load_state()
            for _v in _live_state.values():
                if isinstance(_v, dict) and "name" in _v and "id" in _v:
                    _name_map[_v["id"]] = _v["name"]
                elif isinstance(_v, dict) and "name" in _v:
                    pass
        except Exception:
            pass

        for t in today_data.get("triggers", []):
            # Post-mortem entries key on symphony_name (the producer field name);
            # symphony_id is a secondary fallback for DB-sourced entries (D-DAT-R05).
            sym_id = t.get("symphony_id") or t.get("symphony_name") or t.get("symphony", "")
            sym_name = _name_map.get(sym_id) or t.get("symphony_name") or sym_id
            todays_exits.append(
                {
                    # Finding 11: the producer writes the trigger time as
                    # time_triggered (reporting.py) — timestamp/ts never existed,
                    # so the Time column rendered an em-dash even on this path.
                    "ts": t.get("time_triggered") or t.get("timestamp") or t.get("ts", ""),
                    "symphony_id": sym_id,
                    "symphony_name": sym_name,
                    "reason": t.get("exit_reason", t.get("reason", "")),
                    "detail": t.get("detail", t.get("saved_pct_guard_alpha", "")),
                }
            )
    except (FileNotFoundError, KeyError, _json.JSONDecodeError):
        pass
    stats["todays_exits"] = todays_exits

    return stats


def compute_v1_bootstrap_state(sample_size: int, divergence_detected: bool) -> str:
    """Return V1 bootstrap state string based on shadow_history sample size.

    States (PA-M1F-11 / AC-M1F.6.4):
      'indeterminate'         — sample_size < 30 (below Bailey/de-Prado 2014 threshold)
      'provisional_no_overfit' — sample_size >= 30 and no divergence detected
      'overfit_confirmed'     — sample_size >= 30 and divergence detected
    """
    if sample_size < _V1_BOOTSTRAP_MIN_DAYS:
        return "indeterminate"
    if divergence_detected:
        return "overfit_confirmed"
    return "provisional_no_overfit"
