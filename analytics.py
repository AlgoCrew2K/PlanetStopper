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
from typing import Any

# ---------------------------------------------------------------------------
# Producer -> internal field-name mapping (single source of truth)
# ---------------------------------------------------------------------------
# Source: reporting.py:generate_eod_snapshot trigger dict construction.
# If reporting.py renames any of these, the canary test
# (test_analytics_consumes_full_reporting_schema_without_keyerror) catches it.
_PRODUCER_SYMPHONY_ID = "symphony_name"   # -> dict key
_PRODUCER_VALUE       = "symphony_value"  # -> "value"
_PRODUCER_LIVE_RET    = "shadow_return"   # -> "live_ret"
_PRODUCER_F_RET       = "exit_return"     # -> "f_ret"

# Filename pattern + extraction regex.
_POST_MORTEM_GLOB = "post_mortem_*.json"
_POST_MORTEM_DATE_RE = re.compile(r"post_mortem_(\d{4}-\d{2}-\d{2})\.json$")

# Module-level cache for get_history_with_cache_invalidation.
# Key: (latest_mtime_seen, days, base_dir). Reload on any change.
_HISTORY_CACHE: dict = {"key": None, "data": None}

# Minimum finite observations required for quantstats. < 2 => insufficient data.
# Source: DV1 binding contract.
_MIN_QUANTSTATS_OBSERVATIONS = 2


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
            with open(fpath, "r", encoding="utf-8") as fh:
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
    is the AlphaBot-exited / shadow returns (from f_ret).
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
        max_drawdown, calmar, win_rate.

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

    # Build a daily DatetimeIndex; quantstats infers periodicity from it.
    idx = pd.date_range("2000-01-01", periods=len(finite_vals), freq="D")
    series = pd.Series(finite_vals, index=idx, dtype=float)

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

    return metrics


# ---------------------------------------------------------------------------
# M1 data-layer helpers — per-symphony and portfolio TC / CR / MDD
# ---------------------------------------------------------------------------
# Data-source contract (binding):
#   If-held side sourced from Composer symphony-stats-meta fields.
#   Dry-run side sourced from bot_state (AlphaBot shadow tracking).
#   Network-free: callers pass already-fetched data; no fetch_symphony_stats calls here.

def get_symphony_today_change(
    sym_dict: dict,
    bot_state_entry: "dict | None",
    trading_day: "str | None" = None,
    db_path: "str | None" = None,
) -> dict:
    """
    Per-symphony Today's Change.

    if_held: last_percent_change * 100 (Composer decimal -> percent).
    dry_run: latest shadow_history.shadow_return for (symphony_id, trading_day).
             Returns None sentinel when shadow_history empty — no fall-back to if_held
             (AC-M1F.3.5).
    trading_day: override for today; defaults to sym_dict["trading_day"] then today.
    db_path: override DB file path (for tests).
    """
    if_held = float(sym_dict["last_percent_change"]) * 100.0

    symphony_id = sym_dict.get("id")
    # trading_day priority: explicit kwarg > sym_dict field > today
    _trading_day = trading_day or sym_dict.get("trading_day")
    if not _trading_day:
        from datetime import datetime as _dt, timezone as _tz
        _trading_day = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    dry_run: "float | None" = None
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        row = _load_latest_shadow_row_for_analytics(symphony_id, _trading_day, _db_file)
        if row is not None:
            dry_run = float(row["shadow_return"])
    return {"if_held": if_held, "dry_run": dry_run}


# Patched by tests to redirect DB queries to tmp_path databases.
DB_FILE: "str | None" = None


def _get_shadow_db_file() -> str:
    """Return the shadow_history DB file path; honours test-time DB_FILE override."""
    if DB_FILE is not None:
        return DB_FILE
    import database as _db
    return _db.DB_FILE


def _load_latest_shadow_row_for_analytics(
    symphony_id: str, trading_day: str, db_file: str
) -> "dict | None":
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


def _get_shadow_cumulative_trajectory(
    symphony_id: str, db_file: str
) -> "list[float] | None":
    """Return ordered list of per-day EOD shadow_return values for chain-link CR.

    Day-row selection: last row per trading_day by ts_utc (AC-M1F.3.2).
    Returns None when fewer than 2 distinct trading days exist.
    Result is cached per (symphony_id, db_file) in database._shadow_cr_cache;
    invalidated on new row write (record_shadow_observation).
    """
    import sqlite3
    import database as _db

    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    cache_key = (symphony_id, today, db_file)
    cached = _db._shadow_cr_cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        conn = sqlite3.connect(db_file, timeout=10.0)
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
    bot_state_entry: "dict | None",
    trading_day: "str | None" = None,
    db_path: "str | None" = None,
) -> dict:
    """
    Per-symphony Cumulative Return.

    if_held: simple_return UNLESS (simple_return == 0.0 AND net_deposits == 0.0),
             in which case falls back to time_weighted_return (anomalous withdrawn/re-funded
             symphony where simple_return would be misleadingly zero).
    dry_run: chain-link product over per-day EOD shadow_return values expressed as percent
             (AC-M1F.3.2, PA-M1F-16). None when fewer than 2 distinct trading days in
             shadow_history — no fall-back to if_held (AC-M1F.3.5).

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
    dry_run: "float | None" = None
    if symphony_id:
        _db_file = db_path if db_path is not None else _get_shadow_db_file()
        trajectory = _get_shadow_cumulative_trajectory(symphony_id, _db_file)
        if trajectory is not None:
            product = 1.0
            for r in trajectory:
                product *= 1.0 + r / 100.0
            dry_run = (product - 1.0) * 100.0

    return {"if_held": if_held, "dry_run": dry_run}


def get_symphony_max_drawdown(
    sym_dict: dict,
    bot_state_entry: "dict | None",
    trading_day: "str | None" = None,
    db_path: "str | None" = None,
) -> dict:
    """
    Per-symphony Max Drawdown.

    if_held: max_drawdown from Composer (positive float, magnitude convention).
    dry_run: peak-to-trough drawdown of cumulative shadow trajectory (AC-M1F.3.3).
             None when fewer than 2 distinct trading days — no fall-back to if_held
             (AC-M1F.3.5).

    Returns {"if_held": None, "dry_run": None} when max_drawdown is None (missing data).
    """
    if sym_dict.get("max_drawdown") is None:
        return {"if_held": None, "dry_run": None}
    if_held = float(sym_dict["max_drawdown"])

    symphony_id = sym_dict.get("id")
    dry_run: "float | None" = None
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
    symphonies: "list[dict]",
    bot_state: dict,
    per_sym_fn,
    *,
    none_on_empty: bool = False,
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
        per = per_sym_fn(sym, entry)
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

    dry_run_result: "float | None" = (
        dry_run_wsum / dry_run_weight if dry_run_weight > 0.0 else None
    )
    return {
        "if_held": if_held_wsum / total_weight,
        "dry_run": dry_run_result,
    }


def get_portfolio_today_change(symphonies: "list[dict]", bot_state: dict) -> dict:
    """Value-weighted portfolio Today's Change across all symphonies."""
    return _value_weighted_portfolio(symphonies, bot_state, get_symphony_today_change)


def get_portfolio_cumulative_return(symphonies: "list[dict]", bot_state: dict) -> dict:
    """Value-weighted portfolio Cumulative Return across all symphonies."""
    return _value_weighted_portfolio(
        symphonies, bot_state, get_symphony_cumulative_return, none_on_empty=True
    )


def get_portfolio_max_drawdown(symphonies: "list[dict]", bot_state: dict) -> dict:
    """Value-weighted portfolio Max Drawdown across all symphonies."""
    return _value_weighted_portfolio(
        symphonies, bot_state, get_symphony_max_drawdown, none_on_empty=True
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


# V1 bootstrap gate — three-state fold-sufficiency check (PA-M1F-11, AC-M1F.6.4)
# Threshold: N >= 30 per Bailey/de-Prado 2014 interpretability floor.
_V1_BOOTSTRAP_MIN_DAYS = 30


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
