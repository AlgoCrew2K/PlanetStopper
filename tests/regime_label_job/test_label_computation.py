"""RED tests -- regime_label_job.compute_label: the AC-2 design-basis series and label.

Contract (feature-plans/regime-label-producer.md AC-2, AC-5; approach "Series and label"):

  compute_label(holdings, closes, session, newest_eligible) -> LabelComputation
    holdings        list of {"ticker", "allocation"} as stored (weights used as stored)
    closes          {ticker: {date: close}} -- SIP daily closes (may hold dates >= session; never used)
    session         D
    newest_eligible the newest session whose daily bar is complete at the request end bound
  The series is the REGIME_WINDOW_SESSIONS (20) most recent COMPUTABLE sessions among the 30 strictly
  before D, oldest first, as FRACTIONS. A session is computable when every holding has a close on it and
  on the session before it and it is not later than newest_eligible. A missing close is never filled.
  A present close that is not a finite positive number gives a NaN return (kept in the series).
  Fewer than 20 computable -> label None, reason insufficient_history.
  classify_regime returning None -> label None, reason no_classifier_label.

Expected values come from the fixture closes through ``_sip.oracle_window`` (an independent reference)
and from calling the real ``regime_classifier.classify_regime``, never from literals copied from a run.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import regime_classifier
from tests.regime_label_job import _sip as S

D = S.TARGET_D
NEWEST = S.NEWEST_ELIGIBLE
LABELS = {"trending", "mean-reverting", "high-vol"}
GOLDEN = {
    "trending": ("TRN", "trending_basic"),
    "mean_reverting": ("MRV", "mean_reverting_basic"),
    "high_vol": ("HVL", "high_vol_stressed_basic"),
}


@pytest.fixture()
def job():
    import regime_label_job as mod

    return mod


def _golden_closes(key: str) -> tuple[list[dict], dict, list[float], str]:
    prefix, name = GOLDEN[key]
    fixture = json.loads((S.MATH_DIR / f"{name}.json").read_text(encoding="utf-8"))
    closes = S.closes_from_body(S.load_derived(f"sip_1day_golden_{key}.json")["body"])
    holdings = S.holdings_of((f"{prefix}A", 0.6), (f"{prefix}B", 0.4))
    return holdings, closes, fixture["inputs"]["returns"], fixture["expected"]


def _long_closes(
    tickers_and_seeds, n_sessions: int, last: date = NEWEST
) -> tuple[dict, list[date]]:
    sessions = S.sessions_ending(last, n_sessions)
    closes = {
        t: S.closes_from_returns(S.random_returns(n_sessions - 1, seed), sessions)
        for t, seed in tickers_and_seeds
    }
    return closes, sessions


def _values(result) -> list[float]:
    return list(result.series)


# ---------------------------------------------------------------------------
# S-2.1  the series equals the allocation-weighted close-to-close returns of the fixture closes
# ---------------------------------------------------------------------------


def test_series_equals_weighted_close_to_close_returns_of_the_captured_sip_closes(job):
    closes = S.closes_from_body(S.load_captured("sip_1day")["body"])
    holdings = S.holdings_of(("QQQ", 0.7), ("DULL", 0.3))
    expected = S.oracle_window(holdings, closes, D, NEWEST)
    assert len(expected) == job.REGIME_WINDOW_SESSIONS, (
        "precondition: the capture holds >= 20 computable sessions"
    )

    result = job.compute_label(holdings, closes, D, NEWEST)

    # float association differs between this reference and the module; 1e-12 is far above rounding (~1e-16)
    assert _values(result) == pytest.approx([r for _, r in expected], abs=1e-12)
    assert result.label == regime_classifier.classify_regime([r for _, r in expected])
    assert result.label in LABELS
    assert result.reason is None
    assert result.window_count == job.REGIME_WINDOW_SESSIONS
    assert result.window_first == expected[0][0]
    assert result.window_last == expected[-1][0] == NEWEST


def test_series_from_a_fetched_captured_page_equals_the_independent_computation(job, monkeypatch):
    captured = S.load_captured("sip_1day")
    closes = S.closes_from_body(captured["body"])
    stub = S.AlpacaStub(closes)
    monkeypatch.setattr(requests, "get", stub)
    monkeypatch.setattr(job.time, "sleep", lambda s: None)
    holdings = S.holdings_of(("QQQ", 0.7), ("DULL", 0.3))
    first = job.window_sessions(D)[0]
    end = S.JOB_START - job.SIP_END_LAG

    sip = job.fetch_sip_daily_closes(["DULL", "QQQ"], first, end)
    result = job.compute_label(holdings, sip.closes, D, NEWEST)

    expected = S.oracle_window(holdings, closes, D, NEWEST)
    assert sip.failed == {}
    assert _values(result) == pytest.approx([r for _, r in expected], abs=1e-12)  # see above
    assert result.label == regime_classifier.classify_regime([r for _, r in expected])


# ---------------------------------------------------------------------------
# S-2.2  three golden price histories give the three labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(GOLDEN))
def test_golden_price_history_gets_the_fixtures_own_expected_label(job, key):
    holdings, closes, golden_returns, expected_label = _golden_closes(key)

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label == expected_label
    assert result.label == regime_classifier.classify_regime(golden_returns)
    # 0.6*r + 0.4*r rebuilt through two float close chains stays within 1e-12 of r
    assert _values(result) == pytest.approx(golden_returns, abs=1e-12)
    assert result.reason is None


def test_the_three_golden_histories_give_three_different_labels(job):
    labels = {job.compute_label(*_golden_closes(k)[:2], D, NEWEST).label for k in GOLDEN}
    assert labels == LABELS


# ---------------------------------------------------------------------------
# S-2.3  fractions, not percent: a 2% daily std must not read as high-vol
# ---------------------------------------------------------------------------


def test_two_percent_daily_std_is_labelled_with_fractions_not_percent(job):
    closes = S.closes_from_body(S.load_derived("sip_1day_std_0p02.json")["body"])
    holdings = S.holdings_of(("STDA", 0.6), ("STDB", 0.4))

    result = job.compute_label(holdings, closes, D, NEWEST)

    series = _values(result)
    assert S.sample_std(series) == pytest.approx(0.02, abs=1e-12)  # fixture std is exact to ~1e-15
    assert max(abs(v) for v in series) < 0.05, (
        "returns are fractions (about 0.02), never percent (about 2)"
    )
    assert S.sample_std(series) < regime_classifier.HIGH_VOL_DAILY_THRESHOLD
    assert result.label != "high-vol"
    assert result.label == regime_classifier.classify_regime(series)


# ---------------------------------------------------------------------------
# S-2.4 / DC-5  no lookahead: bars dated D or later never enter the series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_days", [[0], [1], [0, 1, 2, 5]], ids=["D", "after-D", "D-and-later"]
)
def test_bars_dated_on_or_after_the_target_session_never_enter_the_series(job, extra_days):
    holdings, closes, _, _ = _golden_closes("mean_reverting")
    baseline = job.compute_label(holdings, closes, D, NEWEST)

    polluted = copy.deepcopy(closes)
    for ticker in polluted:
        last = polluted[ticker][NEWEST]
        for k in extra_days:
            polluted[ticker][D + timedelta(days=k)] = last * 50.0  # a huge move, if it were used

    result = job.compute_label(holdings, polluted, D, NEWEST)

    assert _values(result) == _values(baseline)
    assert result.label == baseline.label
    assert result.window_last == baseline.window_last == NEWEST
    assert result.window_last < D


def test_a_session_later_than_newest_eligible_is_never_computable(job):
    holdings, closes, golden_returns, expected_label = _golden_closes("mean_reverting")
    full = job.compute_label(holdings, closes, D, NEWEST)
    assert full.label == expected_label

    # With the newest bar not yet complete, the capture holds 19 computable sessions within the 30
    earlier = NEWEST - timedelta(days=1)
    while not S.market_calendar.is_trading_day(earlier):
        earlier -= timedelta(days=1)
    result = job.compute_label(holdings, closes, D, earlier)

    assert result.label is None
    assert result.reason == job.REASON_INSUFFICIENT_HISTORY
    assert len(S.oracle_window(holdings, closes, D, earlier)) == job.REGIME_WINDOW_SESSIONS - 1


# ---------------------------------------------------------------------------
# S-2.5  exactly the 20 most recent
# ---------------------------------------------------------------------------


def test_with_25_computable_sessions_the_series_has_exactly_the_20_most_recent(job):
    closes, sessions = _long_closes([("AAA", 11), ("BBB", 12)], 26)
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    expected = S.oracle_window(holdings, closes, D, NEWEST)
    assert len(S.oracle_window(holdings, closes, D, NEWEST, take=99)) == 25, "precondition"

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert len(result.series) == job.REGIME_WINDOW_SESSIONS
    assert _values(result) == pytest.approx(
        [r for _, r in expected], abs=1e-12
    )  # association order only
    assert result.window_first == expected[0][0]
    assert result.label == regime_classifier.classify_regime([r for _, r in expected])


def test_changing_the_four_oldest_closes_leaves_series_and_label_unchanged(job):
    closes, sessions = _long_closes([("AAA", 11), ("BBB", 12)], 26)
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    baseline = job.compute_label(holdings, closes, D, NEWEST)

    changed = copy.deepcopy(closes)
    for ticker in changed:
        for s in sessions[:4]:
            changed[ticker][s] *= 3.7

    result = job.compute_label(holdings, changed, D, NEWEST)

    assert _values(result) == _values(baseline)
    assert result.label == baseline.label


def test_changing_a_close_inside_the_twenty_session_window_changes_the_series(job):
    closes, sessions = _long_closes([("AAA", 11), ("BBB", 12)], 26)
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    baseline = job.compute_label(holdings, closes, D, NEWEST)

    changed = copy.deepcopy(closes)
    changed["AAA"][sessions[-3]] *= 1.5

    assert _values(job.compute_label(holdings, changed, D, NEWEST)) != _values(baseline)


# ---------------------------------------------------------------------------
# S-2.6  allocation weights, as stored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weights",
    [(0.6, 0.4), (0.3, 0.2), (0.9, 0.1)],
    ids=["sum-1", "sum-below-1-used-as-stored", "lopsided"],
)
def test_each_session_return_is_the_allocation_weighted_sum_of_close_to_close_returns(job, weights):
    closes, sessions = _long_closes([("AAA", 21), ("BBB", 22)], 22)
    holdings = S.holdings_of(("AAA", weights[0]), ("BBB", weights[1]))

    result = job.compute_label(holdings, closes, D, NEWEST)

    series = _values(result)
    used = sessions[-(job.REGIME_WINDOW_SESSIONS + 1) :]
    for i, value in enumerate(series):
        s, prev = used[i + 1], used[i]
        ra = closes["AAA"][s] / closes["AAA"][prev] - 1.0
        rb = closes["BBB"][s] / closes["BBB"][prev] - 1.0
        # float association order only
        assert value == pytest.approx(weights[0] * ra + weights[1] * rb, abs=1e-12)
        mean_return = (ra + rb) / 2.0
        if weights[0] != weights[1] and abs(ra - rb) > 1e-4:
            assert value != pytest.approx(mean_return, abs=1e-6), "not an equal-weighted mean"


def test_a_return_is_a_close_to_close_value_not_an_intraday_one(job):
    closes, sessions = _long_closes([("AAA", 31)], 22)
    holdings = S.holdings_of(("AAA", 1.0))
    result = job.compute_label(holdings, closes, D, NEWEST)
    s, prev = sessions[-1], sessions[-2]
    assert _values(result)[-1] == pytest.approx(
        closes["AAA"][s] / closes["AAA"][prev] - 1.0, abs=1e-12
    )  # rounding


# ---------------------------------------------------------------------------
# S-2.7  the stored holdings apply to every session in the window
# ---------------------------------------------------------------------------


def test_stored_holdings_price_every_session_even_when_an_earlier_set_differed(job):
    entries = json.loads(
        (
            S.REPO_ROOT / "tests" / "fixtures" / "prod_droplet" / "bot_state_positions.json"
        ).read_text(encoding="utf-8")
    )
    entry = next(v for v in entries.values() if isinstance(v, dict) and v.get("current_holdings"))
    stored = [
        {"ticker": h["ticker"], "allocation": h["allocation"]} for h in entry["current_holdings"]
    ]
    tickers = [h["ticker"] for h in stored]
    closes, _ = _long_closes([(t, 100 + i) for i, t in enumerate(tickers)] + [("OLDX", 7)], 24)
    assert "OLDX" not in tickers

    result = job.compute_label(stored, closes, D, NEWEST)

    expected = S.oracle_window(stored, closes, D, NEWEST)
    assert _values(result) == pytest.approx(
        [r for _, r in expected], abs=1e-12
    )  # association order only
    # a different (earlier) set priced the same window would give a different series
    earlier = S.holdings_of(("OLDX", 1.0))
    other = S.oracle_window(earlier, closes, D, NEWEST)
    assert [r for _, r in other] != [r for _, r in expected]


# ---------------------------------------------------------------------------
# S-2.8  200 with no bar for a holding: never a zero return (DC-2)
# ---------------------------------------------------------------------------


def test_a_holding_with_no_bar_in_a_successful_response_leaves_no_computable_session(job):
    closes = S.closes_from_body(S.load_captured("sip_1day_missing_symbol")["body"])
    assert "ZZZQX" not in closes and closes["QQQ"], "precondition: the captured 200 omits ZZZQX"
    holdings = S.holdings_of(("QQQ", 0.5), ("ZZZQX", 0.5))

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label is None
    assert result.reason == job.REASON_INSUFFICIENT_HISTORY
    assert result.label != "mean-reverting"  # a zero-filled constant series would classify this way


# ---------------------------------------------------------------------------
# S-2.10  a missing close removes the sessions that need it; the window reaches further back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("omit_index", [-3, -4, -10], ids=["bar-on-s", "bar-before-s", "deep"])
def test_a_holding_missing_one_session_is_left_out_and_the_window_reaches_back(job, omit_index):
    closes, sessions = _long_closes([("AAA", 41), ("BBB", 42)], 45)
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    baseline = job.compute_label(holdings, closes, D, NEWEST)

    gapped = copy.deepcopy(closes)
    del gapped["BBB"][sessions[omit_index]]
    expected = S.oracle_window(holdings, gapped, D, NEWEST)
    assert len(expected) == job.REGIME_WINDOW_SESSIONS, "precondition: 20 computable remain"

    result = job.compute_label(holdings, gapped, D, NEWEST)

    assert _values(result) == pytest.approx(
        [r for _, r in expected], abs=1e-12
    )  # association order only
    assert result.window_first == expected[0][0]
    assert result.window_first < baseline.window_first, "the window reaches back past the gap"
    assert sessions[omit_index] not in {s for s, _ in expected}
    assert not any(v == 0.0 for v in _values(result)), (
        "no zero is substituted for the missing close"
    )
    assert result.label == regime_classifier.classify_regime([r for _, r in expected])


# ---------------------------------------------------------------------------
# S-2.11 / S-5.2  the window never reaches beyond 30 sessions; 19 fails, 20 labels
# ---------------------------------------------------------------------------


def _with_uncomputable_returns(kill_closes: list[int]):
    closes, sessions = _long_closes([("AAA", 51), ("BBB", 52)], 45)
    for idx in kill_closes:
        del closes["BBB"][sessions[idx]]
    return closes, sessions


def test_nineteen_computable_sessions_in_thirty_give_no_label_even_with_more_further_back(job):
    # isolated missing closes kill two returns each; the adjacent pair kills three: 8 + 3 = 11 of 30
    closes, sessions = _with_uncomputable_returns([-3, -6, -9, -12, -20, -19])
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    assert len(S.oracle_window(holdings, closes, D, NEWEST)) == job.REGIME_WINDOW_SESSIONS - 1, (
        "precondition"
    )
    older = [s for s in sessions[:-30] if s in closes["AAA"] and s in closes["BBB"]]
    assert len(older) >= 10, "precondition: computable sessions exist beyond the 30-session bound"

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label is None
    assert result.reason == job.REASON_INSUFFICIENT_HISTORY


def test_twenty_computable_sessions_in_thirty_give_a_label(job):
    closes, sessions = _with_uncomputable_returns([-3, -6, -9, -12, -15])
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    expected = S.oracle_window(holdings, closes, D, NEWEST)
    assert len(expected) == job.REGIME_WINDOW_SESSIONS, "precondition"

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label == regime_classifier.classify_regime([r for _, r in expected])
    assert result.reason is None
    assert result.window_count == job.REGIME_WINDOW_SESSIONS


def test_a_gap_older_than_thirty_sessions_does_not_matter(job):
    closes, sessions = _long_closes([("AAA", 61), ("BBB", 62)], 45)
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))
    baseline = job.compute_label(holdings, closes, D, NEWEST)
    del closes["BBB"][sessions[-40]]
    result = job.compute_label(holdings, closes, D, NEWEST)
    assert _values(result) == _values(baseline)
    assert result.label == baseline.label


# ---------------------------------------------------------------------------
# S-5.6  a non-finite or non-positive close: no label, reason no_classifier_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", [math.nan, math.inf, 0.0, -5.0], ids=["nan", "inf", "zero", "negative"]
)
def test_a_bad_close_inside_the_window_gives_no_label_and_no_classifier_reason(job, bad):
    holdings, closes, _, _ = _golden_closes("mean_reverting")
    bad_closes = copy.deepcopy(closes)
    victim = S.sessions_ending(NEWEST, 8)[0]
    bad_closes["MRVA"][victim] = bad

    result = job.compute_label(holdings, bad_closes, D, NEWEST)

    assert result.label is None
    assert result.reason == job.REASON_NO_CLASSIFIER_LABEL
    assert result.label != "mean-reverting"
    assert result.window_count == job.REGIME_WINDOW_SESSIONS, "the bad session stays in the window"
    assert any(not math.isfinite(v) for v in _values(result)), (
        "the NaN stays in the series (not filled)"
    )


# ---------------------------------------------------------------------------
# S-5.10  no prices at all: never a zero-filled constant series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("closes", [{}, {"AAA": {}, "BBB": {}}, {"OTHER": {NEWEST: 10.0}}])
def test_no_prices_for_any_holding_gives_no_label(job, closes):
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label is None
    assert result.reason == job.REASON_INSUFFICIENT_HISTORY
    assert result.label != "mean-reverting"


def test_a_constant_price_history_is_labelled_by_the_classifier_not_defaulted_around(job):
    # a genuinely flat price is a real constant series; the job passes it through unchanged
    sessions = S.sessions_ending(NEWEST, 22)
    closes = {"FLAT": {s: 10.0 for s in sessions}}
    result = job.compute_label(S.holdings_of(("FLAT", 1.0)), closes, D, NEWEST)
    assert _values(result) == [0.0] * job.REGIME_WINDOW_SESSIONS
    assert result.label == regime_classifier.classify_regime([0.0] * job.REGIME_WINDOW_SESSIONS)


# ---------------------------------------------------------------------------
# Invariants (hypothesis)
# ---------------------------------------------------------------------------

_returns = st.lists(
    st.floats(min_value=-0.2, max_value=0.2, allow_nan=False), min_size=29, max_size=44
)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(returns=_returns, drop=st.sets(st.integers(min_value=0, max_value=44), max_size=12))
def test_result_is_always_well_formed_for_any_gapped_history(returns, drop):
    import regime_label_job as job

    sessions = S.sessions_ending(NEWEST, len(returns) + 1)
    closes = {"AAA": S.closes_from_returns(returns, sessions)}
    for i in drop:
        if i < len(sessions):
            closes["AAA"].pop(sessions[i], None)
    holdings = S.holdings_of(("AAA", 1.0))

    result = job.compute_label(holdings, closes, D, NEWEST)

    assert result.label is None or result.label in LABELS
    assert len(result.series) <= job.REGIME_WINDOW_SESSIONS
    assert (result.reason is None) == (result.label is not None)
    if result.reason == job.REASON_INSUFFICIENT_HISTORY:
        assert len(S.oracle_window(holdings, closes, D, NEWEST)) < job.REGIME_WINDOW_SESSIONS
    if result.label is not None:
        expected = S.oracle_window(holdings, closes, D, NEWEST)
        # association order only
        assert list(result.series) == pytest.approx([r for _, r in expected], abs=1e-12)
        assert result.window_last == expected[-1][0]
        assert result.window_last < D


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    future=st.dictionaries(
        st.integers(min_value=0, max_value=20), st.floats(min_value=0.01, max_value=1e6), max_size=8
    )
)
def test_the_label_is_independent_of_any_bar_dated_on_or_after_the_target(future):
    import regime_label_job as job

    holdings, closes, _, _ = _golden_closes("trending")
    baseline = job.compute_label(holdings, closes, D, NEWEST)
    polluted = copy.deepcopy(closes)
    for ticker in polluted:
        for k, v in future.items():
            polluted[ticker][D + timedelta(days=k)] = v

    result = job.compute_label(holdings, polluted, D, NEWEST)

    assert list(result.series) == list(baseline.series)
    assert result.label == baseline.label


def test_compute_label_does_not_mutate_its_inputs(job):
    holdings, closes, _, _ = _golden_closes("high_vol")
    holdings_before, closes_before = copy.deepcopy(holdings), copy.deepcopy(closes)
    job.compute_label(holdings, closes, D, NEWEST)
    assert holdings == holdings_before
    assert closes == closes_before


# ---------------------------------------------------------------------------
# S-5.11  every failure case through the real job: no row, and only the three labels are ever stored
# ---------------------------------------------------------------------------

_SWEEP_ID = "xR4tQ9mZ2bL7vN1cK8pW"


def _rows() -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(
            "SELECT symphony_id, label, as_of_date FROM regime_label_cache"
        ).fetchall()
    finally:
        conn.close()


def _sweep_cases():
    holdings, closes, _, _ = _golden_closes("mean_reverting")
    entry = {"name": "Sweep", "current_holdings": holdings}

    nineteen = copy.deepcopy(closes)
    nineteen["MRVA"].pop(S.sessions_ending(NEWEST, 21)[0])  # 19 computable sessions remain

    nan_close = copy.deepcopy(closes)
    nan_close["MRVB"][S.sessions_ending(NEWEST, 5)[0]] = math.nan

    one_ticker_absent = {"MRVA": closes["MRVA"]}

    return {
        "control-labels": (closes, None, True),
        "nineteen-sessions": (nineteen, None, False),
        "nan-close": (nan_close, None, False),
        "one-holding-without-bars": (one_ticker_absent, None, False),
        "no-bars-at-all": ({}, None, False),
        "http-403": (
            closes,
            lambda p: S.Resp(
                403, {"message": "subscription does not permit querying recent SIP data"}
            ),
            False,
        ),
        "http-500-after-retries": (closes, lambda p: S.Resp(500, {"message": "x"}), False),
        "connection-error": (closes, lambda p: requests.exceptions.ConnectionError("boom"), False),
    }, entry


@pytest.mark.parametrize("case", list(_sweep_cases()[0]))
def test_every_failure_leaves_no_row_and_only_valid_labels_are_ever_stored(job, monkeypatch, case):
    cases, entry = _sweep_cases()
    closes, rule, should_write = cases[case]
    monkeypatch.setattr(requests, "get", S.AlpacaStub(closes, rule=rule))
    monkeypatch.setattr(job.time, "sleep", lambda s: None)

    run = job.run_regime_label_job(lambda: {_SWEEP_ID: entry}, clock=lambda: S.JOB_START)

    rows = _rows()
    assert run.session == D
    for _, label, as_of in rows:
        assert label in LABELS
        assert as_of == D.isoformat()
    if should_write:
        assert [(r[0], r[2]) for r in rows] == [(_SWEEP_ID, D.isoformat())]
        assert rows[0][1] == regime_classifier.classify_regime(_golden_closes("mean_reverting")[2])
    else:
        assert rows == []
        assert [o.label for o in run.outcomes] == [None]
        assert all(o.reason for o in run.outcomes)
