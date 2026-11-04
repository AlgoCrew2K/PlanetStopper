"""RED tests -- live label equals the replay's regime tick count where the two day values coincide (S-2.12, S-2.9).

The replay (autotuner._replay_resolve_regime_exit_ticks over day records built by the REAL
synthetic_history.build_replay_day) takes each day's value from the last 1-minute IEX bar; the job takes
close-to-close returns from SIP daily closes. They coincide when every holding's IEX and SIP daily closes
are equal and the last minute bar closes at that daily close (feature plan AC-2, S-2.12). Then the job's
label, mapped through math_engine.apply_regime_exit_adjustment, must equal the replay's resolved count.

Fixtures: tests/fixtures/alpaca/stock_bars_derived/{sip_1day,iex_1day,iex_1min}_golden_<label>.json
(derivations in its PROVENANCE.md; each validated in test_fixture_shapes.py).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date

import pytest
import requests

import autotuner
import math_engine
import regime_classifier
import synthetic_history
from tests.regime_label_job import _sip as S

D = S.TARGET_D
NEWEST = S.NEWEST_ELIGIBLE
GOLDEN = {
    "trending": ("TRN", "trending_basic"),
    "mean_reverting": ("MRV", "mean_reverting_basic"),
    "high_vol": ("HVL", "high_vol_stressed_basic"),
}
SYM_ID = "xR4tQ9mZ2bL7vN1cK8pW"


@pytest.fixture()
def job():
    import regime_label_job as mod

    return mod


def _holdings(key):
    prefix = GOLDEN[key][0]
    return S.holdings_of((f"{prefix}A", 0.6), (f"{prefix}B", 0.4))


def _sip_closes(key):
    return S.closes_from_body(S.load_derived(f"sip_1day_golden_{key}.json")["body"])


def _replay_history(key):
    """dates_data / sorted_dates as autotuner builds them, from real build_replay_day ticks."""
    holdings = _holdings(key)
    iex_day = S.closes_from_body(S.load_derived(f"iex_1day_golden_{key}.json")["body"])
    minute = S.load_derived(f"iex_1min_golden_{key}.json")["body"]["bars"]
    per_day: dict[date, dict[str, list[dict]]] = {}
    for ticker, bars in minute.items():
        for b in sorted(bars, key=lambda b: b["t"]):
            per_day.setdefault(S.bar_date(b["t"]), {}).setdefault(ticker, []).append(b)
    days = sorted(per_day)
    dates_data: dict[str, list[dict]] = {}
    for prev, today in zip(days, days[1:], strict=False):
        date_str = today.isoformat()
        bars_today = per_day[today]
        intraday = {
            date_str: {
                t: [{"t": b["t"], "c": b["c"], "vwap": b["vw"]} for b in bars]
                for t, bars in bars_today.items()
            }
        }
        timestamps = [b["t"] for b in bars_today[holdings[0]["ticker"]]]
        yesterday = {t: iex_day[t][prev] for t in iex_day}
        dates_data[date_str] = synthetic_history.build_replay_day(
            sym_id=SYM_ID,
            date_str=date_str,
            holdings=holdings,
            intraday_by_date=intraday,
            timestamps=timestamps,
            hist_data_up_to_yesterday={},
            yesterday_closes=yesterday,
            spy_today=0.0,
        )
    sorted_dates = sorted(dates_data) + [D.isoformat()]
    return dates_data, sorted_dates, len(sorted_dates) - 1


def _replay_ticks(key) -> int:
    dates_data, sorted_dates, idx = _replay_history(key)
    return autotuner._replay_resolve_regime_exit_ticks(dates_data, sorted_dates, idx)


def _job_ticks(label) -> int:
    return math_engine.apply_regime_exit_adjustment(
        regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
    )


def _rows():
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(
            "SELECT symphony_id, label, as_of_date FROM regime_label_cache"
        ).fetchall()
    finally:
        conn.close()


def _run_job(job, monkeypatch, key):
    monkeypatch.setattr(requests, "get", S.AlpacaStub(_sip_closes(key)))
    monkeypatch.setattr(job.time, "sleep", lambda s: None)
    entry = {"name": f"Parity {key}", "current_holdings": _holdings(key)}
    return job.run_regime_label_job(lambda: {SYM_ID: entry}, clock=lambda: S.JOB_START)


# ---------------------------------------------------------------------------
# S-2.12
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(GOLDEN))
def test_replay_trailing_returns_equal_the_jobs_series_where_the_day_values_coincide(job, key):
    dates_data, sorted_dates, idx = _replay_history(key)
    replay_series = [
        dates_data[d][-1]["return"] / autotuner.RETURN_PCT_TO_FRACTION for d in sorted_dates[:idx]
    ][-regime_classifier.MIN_LABEL_SERIES_LENGTH :]

    result = job.compute_label(_holdings(key), _sip_closes(key), D, NEWEST)

    assert len(replay_series) == regime_classifier.MIN_LABEL_SERIES_LENGTH
    # replay: last bar / yesterday's close, then *100 /100; job: close/close. Same numbers, float order only.
    assert list(result.series) == pytest.approx(replay_series, abs=1e-12)


@pytest.mark.parametrize("key", list(GOLDEN))
def test_the_jobs_label_maps_to_the_replays_resolved_tick_count(job, key):
    result = job.compute_label(_holdings(key), _sip_closes(key), D, NEWEST)

    assert result.label is not None
    assert _job_ticks(result.label) == _replay_ticks(key)


@pytest.mark.parametrize("key", list(GOLDEN))
def test_the_stored_row_of_a_real_job_run_maps_to_the_replays_resolved_tick_count(
    job, monkeypatch, key
):
    run = _run_job(job, monkeypatch, key)

    rows = _rows()
    assert run.session == D
    assert [(r[0], r[2]) for r in rows] == [(SYM_ID, D.isoformat())]
    assert _job_ticks(rows[0][1]) == _replay_ticks(key)


def test_the_three_golden_histories_resolve_to_three_different_tick_counts():
    counts = {key: _replay_ticks(key) for key in GOLDEN}
    assert len(set(counts.values())) == len(GOLDEN), counts  # so an agreement is not vacuous


# ---------------------------------------------------------------------------
# S-2.9  running the job changes nothing in the replay (the replay never reads the label cache)
# ---------------------------------------------------------------------------


def test_replay_resolution_for_every_date_is_unchanged_by_a_job_run_in_the_same_process(
    job, monkeypatch
):
    dates_data, sorted_dates, _ = _replay_history("mean_reverting")
    before = [
        autotuner._replay_resolve_regime_exit_ticks(dates_data, sorted_dates, i)
        for i in range(len(sorted_dates))
    ]

    # the job labels a DIFFERENT (trending) history, so a replay that read the cache would change
    run = _run_job(job, monkeypatch, "trending")

    after = [
        autotuner._replay_resolve_regime_exit_ticks(dates_data, sorted_dates, i)
        for i in range(len(sorted_dates))
    ]
    rows = _rows()
    assert rows and rows[0][1] == run.outcomes[0].label, "precondition: the job wrote its row"
    assert _job_ticks(rows[0][1]) != before[-1], (
        "precondition: the stored label would move the replay"
    )
    assert after == before
