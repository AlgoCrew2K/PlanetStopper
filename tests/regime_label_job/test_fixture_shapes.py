"""Validator for the derived Alpaca bar pages (feature plan: regime-label-producer, Fixture provenance).

The derived pages under tests/fixtures/alpaca/stock_bars_derived/ stand in for responses no capture
holds. Each is checked here against the captured record shape and re-derived from its source, so a
hand-edited or drifted page fails.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.regime_label_job import _sip as S

GOLDEN = {
    "trending": ("TRN", "trending_basic"),
    "mean_reverting": ("MRV", "mean_reverting_basic"),
    "high_vol": ("HVL", "high_vol_stressed_basic"),
}
LAST = date(2026, 11, 3)


def _captured_daily_bar() -> dict:
    return S.load_captured("sip_1day")["body"]["bars"]["QQQ"][0]


def _bar_date(t: str) -> date:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(S.ET).date()


def _by_ticker(record: dict) -> dict[str, dict[date, float]]:
    return {
        sym: {_bar_date(b["t"]): b["c"] for b in bars}
        for sym, bars in record["body"]["bars"].items()
    }


@pytest.mark.parametrize("key", list(GOLDEN))
def test_sip_daily_page_has_the_captured_bar_shape(key):
    record = S.load_derived(f"sip_1day_golden_{key}.json")
    expected_keys = set(_captured_daily_bar())
    assert record["status"] == 200
    for sym, bars in record["body"]["bars"].items():
        assert bars, sym
        for bar in bars:
            assert set(bar) == expected_keys
        stamps = [b["t"] for b in bars]
        assert stamps == sorted(stamps)
        # a daily bar is stamped at 00:00 ET, rendered in UTC
        for b in bars:
            local = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(S.ET)
            assert (local.hour, local.minute, local.second) == (0, 0, 0)
    assert "next_page_token" in record["body"]


@pytest.mark.parametrize("key", list(GOLDEN))
def test_sip_daily_closes_rebuild_from_the_golden_returns(key):
    prefix, golden_name = GOLDEN[key]
    returns = S.load_golden_returns(golden_name)
    sessions = S.sessions_ending(LAST, len(returns) + 1)
    tables = _by_ticker(S.load_derived(f"sip_1day_golden_{key}.json"))
    assert set(tables) == {f"{prefix}A", f"{prefix}B"}
    for table in tables.values():
        assert sorted(table) == sessions
        derived = [table[b] / table[a] - 1.0 for a, b in zip(sessions, sessions[1:], strict=False)]
        # a product chain rebuilt in floats drifts by far less than 1e-12
        assert derived == pytest.approx(returns, abs=1e-12)


def test_std_page_has_sample_std_of_two_percent_as_a_fraction():
    tables = _by_ticker(S.load_derived("sip_1day_std_0p02.json"))
    sessions = sorted(tables["STDA"])
    returns = [
        tables["STDA"][b] / tables["STDA"][a] - 1.0
        for a, b in zip(sessions, sessions[1:], strict=False)
    ]
    assert len(returns) == 20
    # rebuild error of the close chain is far below 1e-12
    assert S.sample_std(returns) == pytest.approx(0.02, abs=1e-12)


@pytest.mark.parametrize("key", list(GOLDEN))
def test_iex_daily_closes_equal_sip_closes(key):
    assert _by_ticker(S.load_derived(f"iex_1day_golden_{key}.json")) == _by_ticker(
        S.load_derived(f"sip_1day_golden_{key}.json")
    )


@pytest.mark.parametrize("key", list(GOLDEN))
def test_iex_minute_last_bar_of_each_session_closes_at_the_sip_daily_close(key):
    sip = _by_ticker(S.load_derived(f"sip_1day_golden_{key}.json"))
    minute = S.load_derived(f"iex_1min_golden_{key}.json")["body"]["bars"]
    assert set(minute) == set(sip)
    for sym, bars in minute.items():
        per_session: dict[date, list[dict]] = {}
        for b in bars:
            per_session.setdefault(_bar_date(b["t"]), []).append(b)
        assert sorted(per_session) == sorted(sip[sym])
        for d, session_bars in per_session.items():
            session_bars.sort(key=lambda b: b["t"])
            assert session_bars[-1]["c"] == sip[sym][d]
            assert len(session_bars) == len({b["t"] for b in session_bars})
            for b in session_bars:
                assert datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(UTC).tzinfo


def test_captured_records_carry_the_shape_the_helpers_assume():
    daily = S.load_captured("sip_1day")
    assert daily["status"] == 200
    assert set(daily["body"]["bars"]) == {"QQQ", "DULL", "STPZ"}
    for sym, bars in daily["body"]["bars"].items():
        for b in bars:
            assert set(b) == set(_captured_daily_bar())
            assert S.bar_stamp(_bar_date(b["t"])) == b["t"], sym
    assert S.load_captured("sip_1day_recent_refused")["status"] == 403
    assert S.load_captured("sip_1day_paged")["body"]["next_page_token"]
    assert "ZZZQX" not in S.load_captured("sip_1day_missing_symbol")["body"]["bars"]
