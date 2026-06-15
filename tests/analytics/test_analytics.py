"""
RED-phase tests for analytics.py — Performance tab data layer.

Binding producer contract:
    reporting.py:generate_eod_snapshot writes one post_mortem_<YYYY-MM-DD>.json
    file per trading day with this on-disk schema:

        {
            "date": "YYYY-MM-DD",
            "summary": {
                "total_monitored": int,
                "total_triggered": int,
                "positive_guard_alpha_count": int,
            },
            "tomorrow_target_holdings": {...} | {"STATUS": "..."},
            "triggers": [
                {
                    "symphony_name": str,        # used as the "symphony_id" key
                    "symphony_value": float,     # internal "value"
                    "account_id": str,
                    "exit_reason": str,
                    "exit_return": float,        # internal "f_ret"
                    "attempted_trigger_level": float,
                    "shadow_return": float,      # internal "live_ret"
                    "shadow_hwm": float,
                    "saved_pct_guard_alpha": float,
                    "saved_dollars": float,
                    "hwm_at_trigger": float,
                    "time_triggered": str,
                    "symphony_vol": float,
                    "strategy_params": dict,
                    "next_day_holdings": list,
                },
                ...
            ],
        }

    The "triggers" list is the source of per-symphony rows for a given date.
    analytics.load_post_mortem_history is responsible for mapping the on-disk
    shape to the internal shape declared in DV1's binding contract:

        {date_str: {symphony_id: {"live_ret", "f_ret", "value", ...full payload}}}

    where for each trigger entry:
        symphony_id := symphony_name
        live_ret    := shadow_return
        f_ret       := exit_return
        value       := symphony_value
    plus the full trigger dict carried forward.

quantstats notes:
    quantstats 0.0.81 / pandas 2.3.2 expected in this environment. We avoid
    pinning specific metric outputs because the upstream library can change
    risk-free assumptions/window defaults between versions; we assert
    structure, presence, and finite/None invariants instead.

Tests intentionally fail in RED — analytics.py does not yet exist.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build fixture post-mortem files matching the producer schema
# ---------------------------------------------------------------------------


def _trigger(
    symphony_name: str,
    exit_return: float,
    shadow_return: float,
    symphony_value: float,
    **overrides: Any,
) -> dict:
    """Construct a single trigger entry matching reporting.py's on-disk schema."""
    base = {
        "symphony_name": symphony_name,
        "symphony_value": symphony_value,
        "account_id": "acct-test",
        "exit_reason": "Trailing Stop",
        "exit_return": exit_return,
        "attempted_trigger_level": 0.0,
        "shadow_return": shadow_return,
        "shadow_hwm": 0.0,
        "saved_pct_guard_alpha": exit_return - shadow_return,
        "saved_dollars": 0.0,
        "hwm_at_trigger": 0.0,
        "time_triggered": "15:54",
        "symphony_vol": 0.0,
        "strategy_params": {},
        "next_day_holdings": [],
    }
    base.update(overrides)
    return base


def _write_post_mortem(
    base_dir: Path,
    date_str: str,
    triggers: list[dict],
    *,
    extra_top_level: dict | None = None,
) -> Path:
    """Write a post_mortem_<date>.json fixture in producer-schema shape."""
    payload = {
        "date": date_str,
        "summary": {
            "total_monitored": len(triggers),
            "total_triggered": len(triggers),
            "positive_guard_alpha_count": sum(
                1 for t in triggers if t.get("saved_pct_guard_alpha", 0.0) > 0
            ),
        },
        "tomorrow_target_holdings": {},
        "triggers": triggers,
    }
    if extra_top_level:
        payload.update(extra_top_level)
    fpath = base_dir / f"post_mortem_{date_str}.json"
    fpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return fpath


def _date_str(day_offset: int, base_year: int = 2026, base_month: int = 1) -> str:
    """ISO date offset from 2026-01-01 (or supplied base)."""
    from datetime import date, timedelta

    return (date(base_year, base_month, 1) + timedelta(days=day_offset)).isoformat()


# ---------------------------------------------------------------------------
# Helpers to read internal-shape fields without coupling to a specific
# implementer key-order (tests assert presence + values, not key ordering).
# ---------------------------------------------------------------------------


def _entry_live_ret(entry: dict) -> float:
    """Internal-shape accessor: live_ret (== producer shadow_return)."""
    return entry["live_ret"]


def _entry_f_ret(entry: dict) -> float:
    return entry["f_ret"]


def _entry_value(entry: dict) -> float:
    return entry["value"]


# ---------------------------------------------------------------------------
# Test 1: load_post_mortem_history happy path (3 files)
# ---------------------------------------------------------------------------


def test_load_post_mortem_history_loads_three_files_from_base_dir(tmp_path):
    """
    Three post_mortem_<date>.json files in base_dir must produce a dict keyed
    by date_str (YYYY-MM-DD) with the symphony payload nested inside.
    """
    from analytics import load_post_mortem_history

    dates = [_date_str(0), _date_str(1), _date_str(2)]
    for d in dates:
        _write_post_mortem(
            tmp_path,
            d,
            [_trigger("sym-alpha", exit_return=1.0, shadow_return=0.5, symphony_value=1000.0)],
        )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))

    assert isinstance(history, dict), (
        f"load_post_mortem_history must return a dict; got {type(history)}"
    )
    assert set(history.keys()) == set(dates), (
        f"history must contain exactly the 3 written dates; "
        f"got {set(history.keys())} vs expected {set(dates)}"
    )
    for d in dates:
        day = history[d]
        assert isinstance(day, dict), (
            f"history[{d!r}] must be a dict keyed by symphony_id; got {type(day)}"
        )
        assert "sym-alpha" in day, (
            f"history[{d!r}] must contain the symphony id 'sym-alpha'; got keys={list(day.keys())}"
        )
        entry = day["sym-alpha"]
        # Internal-shape keys are part of the DV1 binding contract.
        for key in ("live_ret", "f_ret", "value"):
            assert key in entry, (
                f"history[{d!r}]['sym-alpha'] must contain internal key {key!r}; "
                f"got keys={list(entry.keys())}"
            )


# ---------------------------------------------------------------------------
# Test 2: load_post_mortem_history respects days limit
# ---------------------------------------------------------------------------


def test_load_post_mortem_history_respects_days_limit(tmp_path):
    """
    With 65 fixture files on disk and days=60, the loader must return the
    60 MOST RECENT by date-in-filename — not the first 60, not all 65.
    """
    from analytics import load_post_mortem_history

    all_dates = [_date_str(i) for i in range(65)]
    for d in all_dates:
        _write_post_mortem(
            tmp_path,
            d,
            [_trigger("sym-alpha", exit_return=0.0, shadow_return=0.0, symphony_value=1.0)],
        )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))

    assert len(history) == 60, f"days=60 must yield exactly 60 entries; got {len(history)}"
    expected_recent = set(all_dates[-60:])  # the 60 most recent by date
    assert set(history.keys()) == expected_recent, (
        f"days=60 must return the 60 most-recent date_strs; "
        f"got {sorted(history.keys())} vs expected {sorted(expected_recent)}"
    )


# ---------------------------------------------------------------------------
# Test 3: load_post_mortem_history skips malformed JSON
# ---------------------------------------------------------------------------


def test_load_post_mortem_history_silently_skips_malformed_json(tmp_path):
    """
    A malformed post_mortem_*.json must NOT raise. The loader must return
    the good files only and silently skip the bad ones.
    """
    from analytics import load_post_mortem_history

    good_dates = [_date_str(0), _date_str(1)]
    for d in good_dates:
        _write_post_mortem(
            tmp_path,
            d,
            [_trigger("sym-alpha", exit_return=1.0, shadow_return=0.5, symphony_value=100.0)],
        )

    bad_date = _date_str(2)
    bad_path = tmp_path / f"post_mortem_{bad_date}.json"
    bad_path.write_text("{this is not valid json", encoding="utf-8")

    # Must not raise.
    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))

    assert set(history.keys()) == set(good_dates), (
        f"malformed file must be silently skipped; expected only good dates "
        f"{set(good_dates)} but got {set(history.keys())}"
    )
    assert bad_date not in history, f"malformed date {bad_date} must not appear in history"


# ---------------------------------------------------------------------------
# Test 4: list_available_symphonies deduplicates and sorts
# ---------------------------------------------------------------------------


def test_list_available_symphonies_deduplicates_and_sorts(tmp_path):
    """
    sym-A appears on 3 days; sym-B on 1 day; sym-C never. Result must be
    ['sym-A', 'sym-B'] in sorted order with no duplicates and no sym-C.
    """
    from analytics import list_available_symphonies, load_post_mortem_history

    _write_post_mortem(
        tmp_path,
        _date_str(0),
        [
            _trigger("sym-A", 1.0, 0.5, 100.0),
            _trigger("sym-B", 0.5, 0.2, 50.0),
        ],
    )
    _write_post_mortem(
        tmp_path,
        _date_str(1),
        [
            _trigger("sym-A", 2.0, 1.0, 100.0),
        ],
    )
    _write_post_mortem(
        tmp_path,
        _date_str(2),
        [
            _trigger("sym-A", 0.5, 0.25, 100.0),
        ],
    )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    symphonies = list_available_symphonies(history)

    assert symphonies == ["sym-A", "sym-B"], (
        f"list_available_symphonies must dedupe and sort; "
        f"expected ['sym-A', 'sym-B'] but got {symphonies}"
    )


# ---------------------------------------------------------------------------
# Test 5: compute_aggregate_returns weighting math
# ---------------------------------------------------------------------------


def test_compute_aggregate_returns_weights_by_symphony_value(tmp_path):
    """
    Two symphonies on the same day:
        sym-A: value=100, live_ret=0.02
        sym-B: value=400, live_ret=0.01

    Aggregate weighted live return:
        (0.02 * 100 + 0.01 * 400) / (100 + 400) = 6 / 500 = 0.012

    f_ret is also weighted; values pinned below.
    """
    from analytics import compute_aggregate_returns, load_post_mortem_history

    d0 = _date_str(0)
    _write_post_mortem(
        tmp_path,
        d0,
        [
            # exit_return -> f_ret, shadow_return -> live_ret, symphony_value -> value
            _trigger("sym-A", exit_return=0.03, shadow_return=0.02, symphony_value=100.0),
            _trigger("sym-B", exit_return=0.015, shadow_return=0.01, symphony_value=400.0),
        ],
    )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    dates, live_returns, shadow_returns = compute_aggregate_returns(history, weight_by="value")

    assert dates == [d0], f"single-day fixture must produce 1-date list; got {dates}"
    assert len(live_returns) == 1 and len(shadow_returns) == 1, (
        f"parallel-list lengths must match dates; "
        f"got live={len(live_returns)} shadow={len(shadow_returns)}"
    )

    # Tolerance: 1e-9 — pure arithmetic on small floats; any drift indicates
    # an off-by-one weighting bug, not floating-point noise.
    expected_live = (0.02 * 100.0 + 0.01 * 400.0) / (100.0 + 400.0)  # 0.012
    expected_f = (0.03 * 100.0 + 0.015 * 400.0) / (100.0 + 400.0)  # 0.018

    assert live_returns[0] == pytest.approx(expected_live, abs=1e-9), (
        f"weighted live return mismatch: expected {expected_live}, got {live_returns[0]}"
    )
    assert shadow_returns[0] == pytest.approx(expected_f, abs=1e-9), (
        f"weighted f_ret/shadow-list mismatch: expected {expected_f}, got {shadow_returns[0]}"
    )


# ---------------------------------------------------------------------------
# Test 6: compute_aggregate_returns handles missing weight key per-day
# ---------------------------------------------------------------------------


def test_compute_aggregate_returns_skips_symphony_missing_weight_for_that_day(tmp_path):
    """
    If a symphony's value field (symphony_value) is missing on a day,
    that symphony is skipped FOR THAT DAY only. The other symphony's
    return becomes the aggregate (weighted average of one entry == itself).

    On day 0: sym-A has value=100 live_ret=0.02; sym-B has value missing.
    Aggregate live_ret on day 0 must equal 0.02 (sym-A alone).
    """
    from analytics import compute_aggregate_returns, load_post_mortem_history

    d0 = _date_str(0)
    bad_trigger = _trigger("sym-B", exit_return=0.05, shadow_return=0.04, symphony_value=999.0)
    # Drop the weight key entirely so analytics must skip this entry for the day.
    bad_trigger.pop("symphony_value")

    _write_post_mortem(
        tmp_path,
        d0,
        [
            _trigger("sym-A", exit_return=0.03, shadow_return=0.02, symphony_value=100.0),
            bad_trigger,
        ],
    )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    dates, live_returns, shadow_returns = compute_aggregate_returns(history, weight_by="value")

    assert dates == [d0], f"single-day fixture must yield 1 date; got {dates}"
    assert live_returns[0] == pytest.approx(0.02, abs=1e-9), (
        f"sym-B must be skipped (no weight); sym-A alone => live_ret=0.02; got {live_returns[0]}"
    )
    assert shadow_returns[0] == pytest.approx(0.03, abs=1e-9), (
        f"sym-B must be skipped (no weight); sym-A alone => f_ret=0.03; got {shadow_returns[0]}"
    )


# ---------------------------------------------------------------------------
# Test 7: compute_aggregate_returns empty history
# ---------------------------------------------------------------------------


def test_compute_aggregate_returns_empty_history_returns_empty_lists():
    """An empty history dict must return three empty parallel lists."""
    from analytics import compute_aggregate_returns

    dates, live_returns, shadow_returns = compute_aggregate_returns({}, weight_by="value")

    assert dates == [], f"empty history must yield empty dates list; got {dates}"
    assert live_returns == [], f"empty history must yield empty live list; got {live_returns}"
    assert shadow_returns == [], f"empty history must yield empty shadow list; got {shadow_returns}"


# ---------------------------------------------------------------------------
# Test 8: compute_per_symphony_returns filters correctly and sorts by date
# ---------------------------------------------------------------------------


def test_compute_per_symphony_returns_filters_to_one_symphony_sorted_by_date(tmp_path):
    """
    With 2 symphonies x 5 days, requesting sym-A must yield exactly 5 entries
    (sym-A only) in chronological date order. sym-B data must not appear.
    """
    from analytics import compute_per_symphony_returns, load_post_mortem_history

    days = [_date_str(i) for i in range(5)]
    for i, d in enumerate(days):
        # Use a per-day distinct value so we can verify the filter pulled sym-A's row.
        _write_post_mortem(
            tmp_path,
            d,
            [
                _trigger(
                    "sym-A",
                    exit_return=0.01 * (i + 1),
                    shadow_return=0.005 * (i + 1),
                    symphony_value=100.0,
                ),
                _trigger("sym-B", exit_return=0.99, shadow_return=0.99, symphony_value=999.0),
            ],
        )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    out_dates, live_returns, shadow_returns = compute_per_symphony_returns(history, "sym-A")

    assert out_dates == days, (
        f"per-symphony dates must be chronologically sorted; expected {days} got {out_dates}"
    )
    assert len(live_returns) == 5 and len(shadow_returns) == 5, (
        f"parallel-list lengths must match dates count (5); "
        f"got live={len(live_returns)} shadow={len(shadow_returns)}"
    )

    # Each entry must derive from sym-A — fixture-derived expected values:
    # day i -> shadow_return=0.005*(i+1), exit_return=0.01*(i+1)
    for i in range(5):
        expected_live = 0.005 * (i + 1)
        expected_f = 0.01 * (i + 1)
        assert live_returns[i] == pytest.approx(expected_live, abs=1e-9), (
            f"day {i}: live (shadow_return) mismatch — sym-B leaked? "
            f"expected {expected_live} got {live_returns[i]}"
        )
        assert shadow_returns[i] == pytest.approx(expected_f, abs=1e-9), (
            f"day {i}: f_ret (exit_return) mismatch — sym-B leaked? "
            f"expected {expected_f} got {shadow_returns[i]}"
        )


# ---------------------------------------------------------------------------
# Test 9: compute_per_symphony_returns missing symphony returns empty
# ---------------------------------------------------------------------------


def test_compute_per_symphony_returns_unknown_symphony_returns_empty_lists(tmp_path):
    """Requesting a symphony absent from history must yield three empty lists."""
    from analytics import compute_per_symphony_returns, load_post_mortem_history

    _write_post_mortem(
        tmp_path,
        _date_str(0),
        [
            _trigger("sym-A", 0.01, 0.005, 100.0),
        ],
    )

    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    out_dates, live_returns, shadow_returns = compute_per_symphony_returns(
        history, "sym-nonexistent"
    )

    assert out_dates == [], f"unknown symphony must yield empty dates; got {out_dates}"
    assert live_returns == [], f"unknown symphony must yield empty live; got {live_returns}"
    assert shadow_returns == [], f"unknown symphony must yield empty shadow; got {shadow_returns}"


# ---------------------------------------------------------------------------
# Test 10: compute_quantstats_metrics happy path — structural assertions
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_happy_path_returns_all_keys_finite_floats():
    """
    Seed 30 days of synthetic returns; assert all 7 required keys are present
    and each value is either a finite float in a sane range or None.

    We deliberately do NOT pin exact quantstats outputs (the library can change
    its risk-free-rate default, annualization basis, etc. across versions).
    Instead we assert shape + finite + plausible-range.
    """
    pytest.importorskip("quantstats", reason="quantstats is an optional dep — skip when absent")
    from analytics import compute_quantstats_metrics

    # Deterministic synthetic series: small positive drift with realistic noise.
    # Bounded so sharpe/sortino fall in a reasonable range and drawdown < 0.
    returns = [
        0.001,
        -0.002,
        0.003,
        -0.001,
        0.002,
        0.004,
        -0.003,
        0.001,
        0.002,
        -0.001,
        0.003,
        -0.002,
        0.001,
        0.004,
        -0.002,
        0.002,
        -0.001,
        0.003,
        0.001,
        -0.002,
        0.002,
        0.001,
        -0.003,
        0.004,
        -0.001,
        0.002,
        0.001,
        -0.002,
        0.003,
        0.001,
    ]

    metrics = compute_quantstats_metrics(returns, freq="D")

    required_keys = {
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
    }
    assert isinstance(metrics, dict), f"metrics must be a dict; got {type(metrics)}"
    missing = required_keys - set(metrics.keys())
    assert not missing, (
        f"compute_quantstats_metrics must return all required keys; missing {missing}"
    )

    # Every non-None value must be a finite float (not NaN, not Inf).
    for key in required_keys:
        v = metrics[key]
        if v is None:
            continue
        assert isinstance(v, float), (
            f"metrics[{key!r}] must be float or None; got {type(v).__name__}={v!r}"
        )
        assert math.isfinite(v), f"metrics[{key!r}] must be finite (no NaN/Inf); got {v!r}"

    # Plausibility: with 30 small-magnitude returns, sharpe should be bounded.
    # Range [-5, 5] is generous; outside that range strongly suggests a
    # frequency/annualization bug, not a noise tolerance issue.
    if metrics["sharpe"] is not None:
        assert -5.0 <= metrics["sharpe"] <= 5.0, (
            f"sharpe out of plausible range [-5, 5] for tame synthetic data; "
            f"got {metrics['sharpe']} — check freq/annualization handling"
        )

    # max_drawdown for a series with several negatives must be <= 0.
    if metrics["max_drawdown"] is not None:
        assert metrics["max_drawdown"] <= 0.0, (
            f"max_drawdown convention must be <= 0 for any series with a loss; "
            f"got {metrics['max_drawdown']}"
        )

    # win_rate is a probability — bounded property.
    if metrics["win_rate"] is not None:
        assert 0.0 <= metrics["win_rate"] <= 1.0, (
            f"win_rate must be in [0, 1] (or report on a 0-1 scale); "
            f"got {metrics['win_rate']} — if implementer uses 0-100, update this assertion"
        )


# ---------------------------------------------------------------------------
# Test 11: compute_quantstats_metrics insufficient data -> all None
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_insufficient_data_returns_none_for_all_metrics():
    """
    Per the binding contract: < 2 observations must yield None for every metric
    (not raise, not return zeros — None signals 'insufficient data' to the UI).
    """
    from analytics import compute_quantstats_metrics

    required_keys = {
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
    }

    for series in ([], [0.001]):
        metrics = compute_quantstats_metrics(series, freq="D")
        assert isinstance(metrics, dict), (
            f"metrics must be a dict even with insufficient data; got {type(metrics)}"
        )
        assert set(metrics.keys()) >= required_keys, (
            f"metrics keys must always include {required_keys}; "
            f"got {set(metrics.keys())} for series={series!r}"
        )
        for k in required_keys:
            assert metrics[k] is None, (
                f"metrics[{k!r}] must be None for insufficient-data series {series!r}; "
                f"got {metrics[k]!r}"
            )


# ---------------------------------------------------------------------------
# Test 12: compute_quantstats_metrics NaN/Inf handling
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_handles_nan_and_inf_without_corruption():
    """
    Inputs containing NaN must not corrupt metric outputs.

    Reasonable-default behavior pinned: NaN entries are filtered out OR the
    affected metrics return None. Either is acceptable — the test asserts that
    NO metric value is NaN or +/-Inf. (Pinning a single behavior would
    over-constrain the implementer; the invariant 'no NaN/Inf in output' is
    the operator-visible contract.)
    """
    pytest.importorskip("quantstats", reason="quantstats is an optional dep — skip when absent")
    from analytics import compute_quantstats_metrics

    series_with_nan = [
        0.001,
        float("nan"),
        0.002,
        -0.001,
        0.003,
        float("nan"),
        0.002,
        -0.002,
        0.001,
        0.004,
    ]

    metrics = compute_quantstats_metrics(series_with_nan, freq="D")

    assert isinstance(metrics, dict), f"metrics must be a dict; got {type(metrics)}"
    for k, v in metrics.items():
        if v is None:
            continue
        assert isinstance(v, float), (
            f"metrics[{k!r}] must be float or None; got {type(v).__name__}={v!r}"
        )
        assert not math.isnan(v), (
            f"metrics[{k!r}] must not be NaN even when input contains NaN; got {v!r}"
        )
        assert math.isfinite(v), (
            f"metrics[{k!r}] must be finite (no Inf) even when input contains NaN; got {v!r}"
        )


# ---------------------------------------------------------------------------
# Test 13: get_history_with_cache_invalidation — mtime-based reload
# ---------------------------------------------------------------------------


def test_get_history_with_cache_invalidation_reloads_only_when_mtime_changes(tmp_path):
    """
    First call: load_post_mortem_history invoked once.
    Second call (no mtime change): MUST NOT reload — call-count stays at 1.
    Third call (after bumping a fixture's mtime): MUST reload — call-count == 2.

    This guards the dashboard against repeated O(N) disk scans every refresh
    while still picking up the next trading day's snapshot when it lands.
    """
    import analytics

    _write_post_mortem(
        tmp_path,
        _date_str(0),
        [
            _trigger("sym-A", 0.01, 0.005, 100.0),
        ],
    )
    fixture_path = tmp_path / f"post_mortem_{_date_str(0)}.json"

    # Reset module-level cache state if the implementation exposes one.
    # The implementer may name this differently — best-effort reset; the
    # mock-based call-count is the authoritative assertion either way.
    for attr in ("_HISTORY_CACHE", "_history_cache", "_CACHE", "_cache"):
        if hasattr(analytics, attr):
            try:
                setattr(analytics, attr, None)
            except Exception:
                pass

    real_loader = analytics.load_post_mortem_history
    call_counter = {"n": 0}

    def _counting_loader(*args, **kwargs):
        call_counter["n"] += 1
        return real_loader(*args, **kwargs)

    with patch.object(analytics, "load_post_mortem_history", side_effect=_counting_loader):
        # First call: cache miss, loader invoked.
        result1 = analytics.get_history_with_cache_invalidation(days=60, base_dir=str(tmp_path))
        assert isinstance(result1, dict), (
            f"get_history_with_cache_invalidation must return a dict; got {type(result1)}"
        )
        assert call_counter["n"] == 1, (
            f"first call must invoke load_post_mortem_history exactly once; "
            f"counter={call_counter['n']}"
        )

        # Second call with no mtime change: cache hit, no reload.
        result2 = analytics.get_history_with_cache_invalidation(days=60, base_dir=str(tmp_path))
        assert call_counter["n"] == 1, (
            f"second call (no mtime change) must hit cache; counter rose to {call_counter['n']}"
        )
        assert result2 == result1, "cached result must equal initial load"

        # Bump mtime forward so the cache key (latest mtime) changes.
        new_mtime = time.time() + 10.0
        os.utime(fixture_path, (new_mtime, new_mtime))

        # Third call: cache key changed, loader MUST be invoked again.
        analytics.get_history_with_cache_invalidation(days=60, base_dir=str(tmp_path))
        assert call_counter["n"] == 2, (
            f"third call (after mtime bump) must invalidate cache and reload; "
            f"counter={call_counter['n']}"
        )


# ---------------------------------------------------------------------------
# Test 14: Regression canary — schema parity with reporting.py
# ---------------------------------------------------------------------------


def test_analytics_consumes_full_reporting_schema_without_keyerror(tmp_path):
    """
    Cross-file pin: build a fixture trigger entry containing EVERY field that
    reporting.py:generate_eod_snapshot writes (current as of this RED cycle).
    All analytics functions must process it without KeyError / TypeError /
    AttributeError.

    If reporting.py adds a field, this test does not auto-update; analytics
    is then implicitly tolerant of unknown fields. If reporting.py drops or
    renames a field analytics relies on (live_ret <- shadow_return,
    f_ret <- exit_return, value <- symphony_value), the load step's mapping
    fails and this canary catches it.
    """
    from analytics import (
        compute_aggregate_returns,
        compute_per_symphony_returns,
        compute_quantstats_metrics,
        list_available_symphonies,
        load_post_mortem_history,
    )

    # Producer-schema fields — keep in sync with reporting.py:generate_eod_snapshot
    full_trigger = {
        "symphony_name": "sym-canary",
        "symphony_value": 1234.56,
        "account_id": "acct-001",
        "exit_reason": "Trailing Stop",
        "exit_return": 1.23,
        "attempted_trigger_level": 0.5,
        "shadow_return": 0.45,
        "shadow_hwm": 2.10,
        "saved_pct_guard_alpha": 0.78,
        "saved_dollars": 9.87,
        "hwm_at_trigger": 2.30,
        "time_triggered": "15:54",
        "symphony_vol": 0.12,
        "strategy_params": {"trail_pct": 1.5},
        "next_day_holdings": ["AAPL", "MSFT"],
    }

    d0 = _date_str(0)
    _write_post_mortem(
        tmp_path,
        d0,
        [full_trigger],
        extra_top_level={
            # Top-level fields written by generate_eod_snapshot
            "tomorrow_target_holdings": {"AAPL": 0.6, "MSFT": 0.4},
        },
    )

    # Each call must succeed; the assertion is "no exception + plausible shape".
    history = load_post_mortem_history(days=60, base_dir=str(tmp_path))
    assert d0 in history, (
        f"canary date {d0} must load from full producer schema; got keys={list(history.keys())}"
    )
    assert "sym-canary" in history[d0], (
        f"canary symphony must be keyed by symphony_name; got keys={list(history[d0].keys())}"
    )

    syms = list_available_symphonies(history)
    assert "sym-canary" in syms, (
        f"canary symphony must appear in list_available_symphonies; got {syms}"
    )

    dates, live_rets, f_rets = compute_aggregate_returns(history, weight_by="value")
    assert dates == [d0], f"aggregate over canary fixture must yield 1 date; got {dates}"
    # Single-symphony weighted average == that symphony's value.
    assert live_rets[0] == pytest.approx(full_trigger["shadow_return"], abs=1e-9), (
        f"single-symphony aggregate live_ret must equal shadow_return; "
        f"expected {full_trigger['shadow_return']}, got {live_rets[0]}"
    )
    assert f_rets[0] == pytest.approx(full_trigger["exit_return"], abs=1e-9), (
        f"single-symphony aggregate f_ret must equal exit_return; "
        f"expected {full_trigger['exit_return']}, got {f_rets[0]}"
    )

    out_dates, per_live, per_f = compute_per_symphony_returns(history, "sym-canary")
    assert out_dates == [d0], f"per-symphony over canary fixture must yield 1 date; got {out_dates}"
    assert per_live[0] == pytest.approx(full_trigger["shadow_return"], abs=1e-9), (
        f"per-symphony live_ret must derive from shadow_return; "
        f"expected {full_trigger['shadow_return']}, got {per_live[0]}"
    )
    assert per_f[0] == pytest.approx(full_trigger["exit_return"], abs=1e-9), (
        f"per-symphony f_ret must derive from exit_return; "
        f"expected {full_trigger['exit_return']}, got {per_f[0]}"
    )

    # Metrics over a 1-element series should report 'insufficient data' (None)
    # per the binding contract (< 2 observations rule).
    metrics = compute_quantstats_metrics(per_live, freq="D")
    assert isinstance(metrics, dict), (
        f"metrics over canary fixture must be a dict; got {type(metrics)}"
    )
    for k in (
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
    ):
        assert k in metrics, f"metrics must always include key {k!r}; got {list(metrics.keys())}"
        assert metrics[k] is None, (
            f"with only 1 observation, metrics[{k!r}] must be None per contract; got {metrics[k]!r}"
        )
