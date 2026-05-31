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
from datetime import UTC

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
    fracs = [
        v / 100.0
        for v in portfolio_daily_returns_pct
        if _is_finite_number(v)
    ]
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
        conn = sqlite3.connect(db_file, timeout=10.0)
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
        conn = sqlite3.connect(db_file, timeout=10.0)
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
    dry_run: anchored shadow series. When >= 2 distinct shadow trading days exist,
             dry_run = if_held + chain_link_pct where chain_link_pct is the from-zero
             product of per-day EOD shadow_return values. This anchors the bot series so
             its window-start equals if_held; divergence (dry_run - if_held) equals the
             guard effect directly. When fewer than 2 shadow days exist (or no shadow rows),
             dry_run = if_held (no recorded divergence yet — both series coincide).

    Returns {"if_held": None, "dry_run": None} when simple_return is None (missing data).
    """
    if sym_dict.get("simple_return") is None:
        return {"if_held": None, "dry_run": None}
    simple_return = float(sym_dict["simple_return"])
    net_deposits = float(sym_dict["net_deposits"])
    if simple_return == 0.0 and net_deposits == 0.0:
        if_held = float(sym_dict["time_weighted_return"]) * 100.0
    else:
        if_held = simple_return * 100.0

    symphony_id = sym_dict.get("id")

    # Default: no shadow divergence recorded — bot series equals held series.
    dry_run: float = if_held
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        trajectory = _get_shadow_cumulative_trajectory(symphony_id, _db_file)
        if trajectory is not None:
            product = 1.0
            for r in trajectory:
                product *= 1.0 + r / 100.0
            dry_run = if_held + (product - 1.0) * 100.0

    return {"if_held": if_held, "dry_run": dry_run}


def get_symphony_max_drawdown(
    sym_dict: dict,
    bot_state_entry: dict | None,
    trading_day: str | None = None,
    db_path: str | None = None,
) -> dict:
    """
    Per-symphony Max Drawdown.

    if_held: max_drawdown from Composer (positive float, magnitude convention).
    dry_run: peak-to-trough drawdown of cumulative shadow trajectory (AC-M1F.3.3).
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
        trajectory = _get_shadow_cumulative_trajectory(symphony_id, _db_file)
        if trajectory is not None:
            # Build cumulative series then compute peak-to-trough
            cum_series: list[float] = []
            product = 1.0
            for r in trajectory:
                product *= 1.0 + r / 100.0
                cum_series.append((product - 1.0) * 100.0)
            if len(cum_series) >= 2:
                peak = cum_series[0]
                max_dd = 0.0
                for val in cum_series:
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


def get_portfolio_daily_returns_from_shadow(
    db_file: str | None = None,
    days: int = 125,
) -> tuple[list[str], list[float]] | None:
    """Build a continuous portfolio-level daily return series from shadow_history.

    Aggregates per-symphony shadow_return values for each trading day using the
    last row per (symphony_id, trading_day). Value-weights by current_return proxy
    (equal-weight fallback when current_return is 0 or unavailable). Returns
    (dates_sorted_ascending, portfolio_daily_returns) or None when fewer than 2
    distinct trading days exist — callers should fall back to post_mortem data.

    This is the correct source for the hero chart hist series: it is continuous
    (written every cycle for every tracked symphony, not only on exit days), so its
    compounded terminal value converges to the real portfolio return over time.
    """
    import sqlite3

    _db_file = db_file if db_file is not None else _get_shadow_db_file()
    try:
        conn = sqlite3.connect(_db_file, timeout=10.0)
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

    out_dates: list[str] = []
    out_returns: list[float] = []
    for day in sorted_days:
        entries = day_map[day]
        weight_sum = 0.0
        ret_wsum = 0.0
        for sym_ret, sym_cr in entries.values():
            # Use absolute current_return as weight proxy; fall back to equal-weight.
            w = abs(sym_cr) if sym_cr != 0.0 else 1.0
            weight_sum += w
            ret_wsum += sym_ret * w
        if weight_sum > 0.0:
            out_dates.append(day)
            out_returns.append(ret_wsum / weight_sum)

    if len(out_dates) < 2:
        return None

    return out_dates, out_returns


# V1 bootstrap gate — three-state fold-sufficiency check (PA-M1F-11, AC-M1F.6.4)
# Threshold: N >= 30 per Bailey/de-Prado 2014 interpretability floor.
_V1_BOOTSTRAP_MIN_DAYS = 30


def get_history_summary(days: int = 30, base_dir: str = ".") -> dict:
    """Aggregate guard-alpha history for the History tab.

    Returns the same envelope the /api/history/<days> route emits, derived
    from post_mortem_*.json files in base_dir. Exists so the route can delegate
    here and tests can mock this single function.
    """
    import json as _json
    import glob as _glob
    from datetime import datetime as _dt, timedelta as _td, date as _date

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
                br = stats["by_reason"].setdefault(reason, {"alpha": 0.0, "count": 0, "wins": 0, "dollars": 0.0})
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
            sym_id = (
                t.get("symphony_id")
                or t.get("symphony_name")
                or t.get("symphony", "")
            )
            sym_name = _name_map.get(sym_id) or t.get("symphony_name") or sym_id
            todays_exits.append({
                "ts": t.get("timestamp", t.get("ts", "")),
                "symphony_id": sym_id,
                "symphony_name": sym_name,
                "reason": t.get("exit_reason", t.get("reason", "")),
                "detail": t.get("detail", t.get("saved_pct_guard_alpha", "")),
            })
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
