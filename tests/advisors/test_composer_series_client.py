"""RED tests -- advisors/composer_series_client.py (NEW; Cycle 2e AC-1, AC-10).

Module under test: ``advisors.composer_series_client``. The ModuleNotFoundError
raised by the ``client_mod`` fixture is the first RED signal; each test
imports lazily so every test fails individually for the right reason.

CONTRACT (feature-plans/retirement-visibility.md AC-1; pinned in
.claude/tdd-handoff.md): ``fetch_symphony_daily_returns(account_id,
symphony_id) -> SeriesResult`` issues ONE kind of request --
``requests.get(f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/
symphonies/{symphony_id}", headers=get_composer_headers(), timeout=...)`` --
parses ``epoch_ms`` + ``deposit_adjusted_series`` (never the raw ``series``,
which jumps on deposits/withdrawals), and returns the simple daily return in
PERCENT scale keyed by America/New_York calendar date. It never raises.

All HTTP is mocked (``requests.get`` is patched) and ``time.sleep`` is patched
-- no live network and no real sleeping from any test in this file. The
golden values are hand-computed from tiny series; the fixture-driven tests
recompute their expectations independently from the fixture's own arrays.

[Unverified] note (PM, docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md:71):
the semantics of ``deposit_adjusted_series`` are inference, not evidence. These
tests therefore treat it as a positive VALUE-scale series (ratio returns), as
the plan specifies -- nothing here asserts what it "means" economically. The
fixture file is ``_fixture_provenance: "synthetic-from-docs"``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import pathlib
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "composer" / "symphony_daily_series_v1.json"

_ET = ZoneInfo("America/New_York")
_ACCOUNT = "acct-test-1111"
_SYMPHONY = "sym-test-2222"

# Fake credentials -- the D-1 tests assert these never surface in an error
# string or a log record.
_SECRET_KEY_ID = "KEYID-ABC-9f3d"
_SECRET_TOKEN = "Bearer SECRET-TOKEN-XYZ-77c1"
_FAKE_HEADERS = {"x-api-key-id": _SECRET_KEY_ID, "authorization": _SECRET_TOKEN}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_ms(year, month, day, hour=21, minute=0) -> int:
    """Epoch milliseconds for a UTC wall time (the API's epoch_ms unit)."""
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _et_ms(iso_date: str, hour: int = 16, minute: int = 0) -> int:
    """Epoch milliseconds for an America/New_York wall time on `iso_date`."""
    d = date.fromisoformat(iso_date)
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=_ET).timestamp() * 1000)


def _body(epoch_ms, adjusted, raw=None) -> dict:
    return {
        "epoch_ms": list(epoch_ms),
        "series": list(raw) if raw is not None else list(adjusted),
        "deposit_adjusted_series": list(adjusted),
    }


def _resp(status=200, body=None, headers=None, text="") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = CaseInsensitiveDict(headers or {})
    resp.ok = 200 <= status < 300
    if body is None:
        resp.json.side_effect = ValueError("no JSON body")
    else:
        resp.json.return_value = body
    return resp


class _Http:
    """Scriptable stand-in for ``requests.get``: replays `script` (responses or
    exceptions to raise) in order, repeating the last entry once exhausted."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        item = self.script[idx]
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def url(self) -> str:
        args, kwargs = self.calls[0]
        return args[0] if args else kwargs["url"]


@pytest.fixture()
def client_mod(monkeypatch):
    """Import the module under test and neutralise its side-effect seams:
    ``time.sleep`` is recorded (never really slept) and ``get_composer_headers``
    returns fake credentials. Any non-GET ``requests`` verb raises so a stray
    POST/PUT is a loud failure, not a silent live call."""
    import advisors.composer_series_client as mod  # noqa: PLC0415

    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))
    monkeypatch.setattr(mod, "get_composer_headers", lambda: dict(_FAKE_HEADERS))

    def _forbidden(*a, **kw):
        raise AssertionError("composer_series_client must only issue GET requests")

    for verb in ("post", "put", "patch", "delete", "head", "request"):
        monkeypatch.setattr(requests, verb, _forbidden)
    mod._test_sleeps = sleeps  # noqa: SLF001 -- test-only stash for assertions
    return mod


def _fetch(mod, http, monkeypatch, account=_ACCOUNT, symphony=_SYMPHONY):
    monkeypatch.setattr(requests, "get", http)
    return mod.fetch_symphony_daily_returns(account, symphony)


def _et_today() -> date:
    return datetime.now(_ET).date()


# ===========================================================================
# SeriesResult dataclass + module constants (AC-1 shape)
# ===========================================================================


class TestSeriesResultShape:
    def test_series_result_is_a_dataclass_with_exactly_the_documented_fields(self, client_mod):
        assert dataclasses.is_dataclass(client_mod.SeriesResult)
        names = {f.name for f in dataclasses.fields(client_mod.SeriesResult)}
        assert names == {"returns", "points", "first_date", "last_date", "error"}

    def test_series_result_can_be_built_from_returns_and_error_alone(self, client_mod):
        # The plan constructs failures as SeriesResult(returns={}, error=<reason>).
        res = client_mod.SeriesResult(returns={}, error="HTTP 500")
        assert res.returns == {}
        assert res.error == "HTTP 500"
        assert res.points == 0
        assert res.first_date is None
        assert res.last_date is None

    def test_module_exposes_positive_retry_and_timeout_constants(self, client_mod):
        assert isinstance(client_mod.MAX_ATTEMPTS, int)
        assert client_mod.MAX_ATTEMPTS >= 2, "a bounded retry needs at least 2 attempts"
        assert client_mod.REQUEST_TIMEOUT_SECONDS > 0
        assert client_mod.RETRY_AFTER_CAP_SECONDS > 0


# ===========================================================================
# Request contract
# ===========================================================================


class TestRequestContract:
    def _ok_body(self):
        return _body([_utc_ms(2026, 1, 5), _utc_ms(2026, 1, 6)], [100.0, 101.0])

    def test_requests_the_account_scoped_symphony_series_endpoint(self, client_mod, monkeypatch):
        http = _Http([_resp(200, self._ok_body())])
        _fetch(client_mod, http, monkeypatch)

        expected = (
            f"{client_mod.COMPOSER_BASE_URL}/portfolio/accounts/{_ACCOUNT}/symphonies/{_SYMPHONY}"
        )
        assert http.url == expected
        assert len(http.calls) == 1, "a clean 200 must be a single request"

    def test_sends_composer_auth_headers_and_an_explicit_timeout(self, client_mod, monkeypatch):
        http = _Http([_resp(200, self._ok_body())])
        _fetch(client_mod, http, monkeypatch)

        _args, kwargs = http.calls[0]
        assert kwargs["headers"] == _FAKE_HEADERS
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
        assert timeout == client_mod.REQUEST_TIMEOUT_SECONDS
        assert timeout > 0

    def test_a_successful_first_attempt_never_sleeps(self, client_mod, monkeypatch):
        _fetch(client_mod, _Http([_resp(200, self._ok_body())]), monkeypatch)
        assert client_mod._test_sleeps == []  # noqa: SLF001

    def test_only_get_is_ever_issued(self, client_mod, monkeypatch):
        # client_mod's fixture makes every non-GET verb raise AssertionError;
        # a clean parse therefore proves no other verb was attempted.
        res = _fetch(client_mod, _Http([_resp(200, self._ok_body())]), monkeypatch)
        assert res.error is None


# ===========================================================================
# Parsing: golden percent-scale returns, deposit-adjusted source, ET dates
# ===========================================================================


class TestGoldenParsing:
    def test_returns_are_simple_daily_returns_in_percent_scale(self, client_mod, monkeypatch):
        """Hand-computed golden: values 100 -> 102 -> 101 -> 103.02 -> 103.02.

        day1 = (102/100 - 1) * 100 = 2.0
        day2 = (101/102 - 1) * 100 = -0.98039215686...
        day3 = (103.02/101 - 1) * 100 = 2.0
        day4 = (103.02/103.02 - 1) * 100 = 0.0
        """
        epochs = [_utc_ms(2026, 2, 2 + i) for i in range(5)]  # Mon..Fri, 21:00Z = 16:00 EST
        values = [100.0, 102.0, 101.0, 103.02, 103.02]
        res = _fetch(client_mod, _Http([_resp(200, _body(epochs, values))]), monkeypatch)

        assert res.error is None
        assert list(res.returns) == ["2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        got = list(res.returns.values())
        # abs=1e-9: pure float ratio arithmetic; tolerance only absorbs the last-ulp
        # difference between (v1/v0 - 1) * 100 evaluated in different orders.
        assert got[0] == pytest.approx(2.0, abs=1e-9)
        assert got[1] == pytest.approx((101.0 / 102.0 - 1.0) * 100.0, abs=1e-9)
        assert got[1] == pytest.approx(-0.98039215686, abs=1e-9)
        assert got[2] == pytest.approx(2.0, abs=1e-9)
        assert got[3] == pytest.approx(0.0, abs=1e-9)

    def test_percent_scale_means_a_ten_percent_gain_is_ten_not_point_one(
        self, client_mod, monkeypatch
    ):
        epochs = [_utc_ms(2026, 2, 2), _utc_ms(2026, 2, 3)]
        res = _fetch(client_mod, _Http([_resp(200, _body(epochs, [200.0, 220.0]))]), monkeypatch)
        (only,) = res.returns.values()
        assert only == pytest.approx(10.0, abs=1e-9)  # abs=1e-9: exact-ratio arithmetic

    def test_metadata_matches_the_returns_it_describes(self, client_mod, monkeypatch):
        epochs = [_utc_ms(2026, 2, 2 + i) for i in range(4)]
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body(epochs, [100.0, 101.0, 102.0, 103.0]))]),
            monkeypatch,
        )
        assert res.error is None
        assert res.points == len(res.returns) == 3
        assert res.first_date == min(res.returns) == "2026-02-03"
        assert res.last_date == max(res.returns) == "2026-02-05"

    def test_uses_deposit_adjusted_series_never_the_raw_series(self, client_mod, monkeypatch):
        """A +5000 deposit lands in the raw `series` on day 2; the
        deposit-adjusted series is smooth. Returns must follow the latter."""
        epochs = [_utc_ms(2026, 2, 2 + i) for i in range(3)]
        adjusted = [1000.0, 1010.0, 1020.1]
        raw = [1000.0, 6010.0, 6020.1]  # +5000 deposit on day 2
        res = _fetch(client_mod, _Http([_resp(200, _body(epochs, adjusted, raw))]), monkeypatch)

        assert res.error is None
        assert list(res.returns.values()) == [
            pytest.approx(1.0, abs=1e-9),  # abs=1e-9: exact-ratio arithmetic
            pytest.approx(1.0, abs=1e-9),
        ]
        # A raw-series implementation would report a ~+501% "return" on day 2.
        assert max(abs(v) for v in res.returns.values()) < 5.0

    def test_unknown_extra_keys_in_the_response_are_ignored(self, client_mod, monkeypatch):
        body = _body([_utc_ms(2026, 2, 2), _utc_ms(2026, 2, 3)], [100.0, 101.0])
        body["some_future_field"] = {"x": 1}
        res = _fetch(client_mod, _Http([_resp(200, body)]), monkeypatch)
        assert res.error is None
        assert len(res.returns) == 1


class TestEasternDateConversion:
    def test_edt_point_after_utc_midnight_belongs_to_the_previous_et_date(
        self, client_mod, monkeypatch
    ):
        """DST began 2026-03-08 (EDT = UTC-4). 2026-03-10 03:30Z is 2026-03-09
        23:30 EDT -> ET date 03-09 (UTC date would be 03-10)."""
        epochs = [
            _utc_ms(2026, 3, 6, 21, 0),  # Fri 16:00 EST  -> 03-06
            _utc_ms(2026, 3, 10, 3, 30),  # Mon 23:30 EDT  -> 03-09 (UTC date 03-10)
            _utc_ms(2026, 3, 11, 3, 30),  # Tue 23:30 EDT  -> 03-10 (UTC date 03-11)
        ]
        res = _fetch(
            client_mod, _Http([_resp(200, _body(epochs, [100.0, 102.0, 104.04]))]), monkeypatch
        )
        assert res.error is None
        assert list(res.returns) == ["2026-03-09", "2026-03-10"]

    def test_est_point_after_utc_midnight_belongs_to_the_previous_et_date(
        self, client_mod, monkeypatch
    ):
        """2026-01-15 04:30Z is 2026-01-14 23:30 EST -> ET date 01-14."""
        epochs = [
            _utc_ms(2026, 1, 12, 21, 0),  # Mon 16:00 EST -> 01-12
            _utc_ms(2026, 1, 15, 4, 30),  # Wed 23:30 EST -> 01-14 (UTC date 01-15)
            _utc_ms(2026, 1, 16, 4, 30),  # Thu 23:30 EST -> 01-15 (UTC date 01-16)
        ]
        res = _fetch(
            client_mod, _Http([_resp(200, _body(epochs, [100.0, 102.0, 104.04]))]), monkeypatch
        )
        assert res.error is None
        assert list(res.returns) == ["2026-01-14", "2026-01-15"]

    def test_keys_are_iso_calendar_date_strings(self, client_mod, monkeypatch):
        epochs = [_utc_ms(2026, 2, 2), _utc_ms(2026, 2, 3)]
        res = _fetch(client_mod, _Http([_resp(200, _body(epochs, [100.0, 101.0]))]), monkeypatch)
        for key in res.returns:
            assert isinstance(key, str)
            assert date.fromisoformat(key).isoformat() == key


class TestDuplicateAndTodayHandling:
    def test_several_points_on_one_et_date_collapse_to_the_last_before_returns(
        self, client_mod, monkeypatch
    ):
        """02-03 has two points (10:00 -> 101, 16:00 -> 110). The day's value
        is the LAST point (110); returns are then close-to-close between
        consecutive dates: 02-03 = 110/100 - 1 = 10%, 02-04 = 121/110 - 1 = 10%.

        (An implementation that returns-then-dedupes would report 02-03 as
        110/101 - 1 = 8.91% and is wrong.)"""
        epochs = [
            _et_ms("2026-02-02", 16),
            _et_ms("2026-02-03", 10),
            _et_ms("2026-02-03", 16),
            _et_ms("2026-02-04", 16),
        ]
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body(epochs, [100.0, 101.0, 110.0, 121.0]))]),
            monkeypatch,
        )
        assert res.error is None
        assert list(res.returns) == ["2026-02-03", "2026-02-04"]
        assert res.returns["2026-02-03"] == pytest.approx(10.0, abs=1e-9)  # abs: exact ratio
        assert res.returns["2026-02-04"] == pytest.approx(10.0, abs=1e-9)

    def test_points_dated_today_et_are_dropped(self, client_mod, monkeypatch):
        """A midday 'Run now' must never use a partial day: no return keyed at
        today's ET date (clock-relative -- derived, never hardcoded)."""
        for _attempt in range(2):  # guard the (rare) ET-midnight race
            today_before = _et_today()
            days = [today_before - timedelta(days=k) for k in (3, 2, 1, 0)]
            epochs = [_et_ms(d.isoformat(), 12) for d in days]
            res = _fetch(
                client_mod,
                _Http([_resp(200, _body(epochs, [100.0, 101.0, 102.01, 103.0301]))]),
                monkeypatch,
            )
            if _et_today() == today_before:
                break
        assert res.error is None
        assert today_before.isoformat() not in res.returns
        assert list(res.returns) == [days[1].isoformat(), days[2].isoformat()]
        assert res.last_date == days[2].isoformat()

    def test_a_series_whose_only_new_point_is_today_yields_no_todays_return(
        self, client_mod, monkeypatch
    ):
        for _attempt in range(2):
            today_before = _et_today()
            yesterday = today_before - timedelta(days=1)
            epochs = [_et_ms(yesterday.isoformat(), 12), _et_ms(today_before.isoformat(), 12)]
            res = _fetch(
                client_mod, _Http([_resp(200, _body(epochs, [100.0, 130.0]))]), monkeypatch
            )
            if _et_today() == today_before:
                break
        assert today_before.isoformat() not in res.returns
        assert res.returns == {}


class TestNonPositiveAndNonFiniteValues:
    def test_a_return_is_skipped_when_either_endpoint_is_zero(self, client_mod, monkeypatch):
        """Symphony emptied then re-funded: [100, 110, 0, 50, 55].

        PM ruling (RED addendum): skip the return when EITHER endpoint is
        non-positive. Day 3 (v_t == 0) must NOT emit a fabricated -100% day,
        day 4 (v_{t-1} == 0) must not divide by zero; the healthy days around
        the gap (110/100 -> 10%, 55/50 -> 10%) are kept."""
        dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        epochs = [_et_ms(d, 16) for d in dates]
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body(epochs, [100.0, 110.0, 0.0, 50.0, 55.0]))]),
            monkeypatch,
        )
        assert res.error is None
        assert "2026-02-04" not in res.returns, "v_t == 0 -> no fabricated -100% day"
        assert "2026-02-05" not in res.returns, "v_{t-1} == 0 -> return must be skipped"
        assert list(res.returns) == ["2026-02-03", "2026-02-06"]
        assert res.returns["2026-02-03"] == pytest.approx(10.0, abs=1e-9)  # abs: exact ratio
        assert res.returns["2026-02-06"] == pytest.approx(10.0, abs=1e-9)
        assert all(math.isfinite(v) for v in res.returns.values())

    def test_a_return_is_skipped_when_either_endpoint_is_negative(self, client_mod, monkeypatch):
        dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05"]
        epochs = [_et_ms(d, 16) for d in dates]
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body(epochs, [100.0, -5.0, 50.0, 55.0]))]),
            monkeypatch,
        )
        assert res.error is None
        assert "2026-02-03" not in res.returns, "v_t < 0 -> return must be skipped"
        assert "2026-02-04" not in res.returns, "v_{t-1} < 0 -> return must be skipped"
        assert list(res.returns) == ["2026-02-05"]
        assert res.returns["2026-02-05"] == pytest.approx(10.0, abs=1e-9)  # abs: exact ratio
        assert all(math.isfinite(v) for v in res.returns.values())

    def test_a_zero_value_never_emits_a_minus_100_percent_return(self, client_mod, monkeypatch):
        dates = ["2026-02-02", "2026-02-03", "2026-02-04"]
        epochs = [_et_ms(d, 16) for d in dates]
        res = _fetch(client_mod, _Http([_resp(200, _body(epochs, [100.0, 0.0, 0.0]))]), monkeypatch)
        assert res.error is None
        assert res.returns == {}
        assert res.points == 0

    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        values=st.lists(
            # Exact zeros, negatives, and positives bounded away from 0: a positive-to-positive
            # return of ~-100% would round to exactly -100.0 for a denormal-scale value (a float
            # artefact, not a fabricated day), so positives stay >= 1e-3 (min ratio 1e-9).
            st.one_of(
                st.just(0.0),
                st.floats(min_value=-50.0, max_value=-1e-3),
                st.floats(min_value=1e-3, max_value=1e6),
            ),
            min_size=2,
            max_size=30,
        )
    )
    def test_no_return_at_or_below_minus_100_percent_is_ever_emitted(self, client_mod, values):
        """For ANY value series (zeros and negatives included): every emitted
        return is finite and strictly above -100% -- a return with both
        endpoints positive can never reach -100%, so anything <= -100 would be
        a fabricated observation."""
        start = date(2025, 3, 3)
        epochs = [_et_ms((start + timedelta(days=i)).isoformat(), 16) for i in range(len(values))]
        http = _Http([_resp(200, _body(epochs, values))])
        with patch.object(requests, "get", http):
            res = client_mod.fetch_symphony_daily_returns(_ACCOUNT, _SYMPHONY)
        assert res.error is None
        assert all(math.isfinite(r) and r > -100.0 for r in res.returns.values())

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_values_are_skipped_never_propagated(self, client_mod, monkeypatch, bad):
        dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        epochs = [_et_ms(d, 16) for d in dates]
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body(epochs, [100.0, 101.0, bad, 50.0, 55.0]))]),
            monkeypatch,
        )
        assert all(math.isfinite(v) for v in res.returns.values()), (
            "a NaN/inf return must never be emitted"
        )
        # 50 -> 55 has a finite positive previous value and must survive.
        assert res.returns["2026-02-06"] == pytest.approx(10.0, abs=1e-9)  # abs: exact ratio
        # The return that would have used the bad value as its previous value is skipped.
        assert "2026-02-05" not in res.returns


# ===========================================================================
# Fixture-driven parsing (AC-10)
# ===========================================================================


@pytest.fixture(scope="module")
def series_fixture() -> dict:
    assert FIXTURE_PATH.exists(), f"missing fixture {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class TestFixtureFile:
    def test_fixture_has_the_three_documented_response_keys_with_equal_lengths(
        self, series_fixture
    ):
        for key in ("epoch_ms", "series", "deposit_adjusted_series"):
            assert isinstance(series_fixture[key], list) and series_fixture[key], key
        n = len(series_fixture["epoch_ms"])
        assert len(series_fixture["series"]) == n
        assert len(series_fixture["deposit_adjusted_series"]) == n

    def test_fixture_declares_synthetic_provenance_and_a_recapture_note(self, series_fixture):
        assert series_fixture["_fixture_provenance"] == "synthetic-from-docs"
        note = series_fixture.get("_note", "")
        assert isinstance(note, str) and note
        assert "api-fixture" in note, "the note must point at the /api-fixture recapture path"

    def test_fixture_epoch_values_are_epoch_milliseconds(self, series_fixture):
        assert all(isinstance(v, int) and v >= 1e11 for v in series_fixture["epoch_ms"])

    def test_fixture_raw_series_differs_from_adjusted_on_flow_days(self, series_fixture):
        """The fixture must actually discriminate raw vs deposit-adjusted:
        at least one deposit/withdrawal day where the two series diverge."""
        raw, adj = series_fixture["series"], series_fixture["deposit_adjusted_series"]
        assert any(abs(r - a) > 1.0 for r, a in zip(raw, adj, strict=True))

    def test_fixture_dates_are_safely_in_the_past(self, series_fixture):
        last = datetime.fromtimestamp(series_fixture["epoch_ms"][-1] / 1000, tz=UTC).astimezone(_ET)
        assert last.date() < _et_today() - timedelta(days=2), (
            "fixture must not end near 'today' or the today-drop rule would eat its tail"
        )


class TestFixtureDrivenParse:
    def _expected_returns(self, fx: dict) -> dict[str, float]:
        """Independent oracle: ET date per point, last-point-per-date, ratio
        return in percent -- written against the plan, not the client."""
        by_date: dict[str, float] = {}
        for ms, value in zip(fx["epoch_ms"], fx["deposit_adjusted_series"], strict=True):
            d = datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(_ET).date().isoformat()
            by_date[d] = value
        dates = sorted(by_date)
        return {
            cur: (by_date[cur] / by_date[prev] - 1.0) * 100.0
            for prev, cur in zip(dates, dates[1:], strict=False)
        }

    def test_fixture_parses_to_the_independently_recomputed_returns(
        self, client_mod, monkeypatch, series_fixture
    ):
        res = _fetch(client_mod, _Http([_resp(200, series_fixture)]), monkeypatch)
        expected = self._expected_returns(series_fixture)
        assert res.error is None
        assert list(res.returns) == list(expected)
        for key, want in expected.items():
            # abs=1e-9: same formula, same operands -> differences are last-ulp only.
            assert res.returns[key] == pytest.approx(want, abs=1e-9), key
        assert res.points == len(expected)

    def test_fixture_flow_days_do_not_leak_into_returns(
        self, client_mod, monkeypatch, series_fixture
    ):
        res = _fetch(client_mod, _Http([_resp(200, series_fixture)]), monkeypatch)
        # With deposits of thousands on a ~10k base, a raw-series parse would
        # emit a return of tens of percent; the adjusted series' daily sd is ~1%.
        assert max(abs(v) for v in res.returns.values()) < 10.0


# ===========================================================================
# Malformed bodies -> error, never an exception
# ===========================================================================

_E = [_utc_ms(2026, 2, 2 + i) for i in range(3)]


def _bad_bodies():
    good_adj = [100.0, 101.0, 102.0]
    yield (
        "length_mismatch",
        {"epoch_ms": _E, "series": good_adj, "deposit_adjusted_series": good_adj[:2]},
    )
    yield "missing_epoch_ms", {"series": good_adj, "deposit_adjusted_series": good_adj}
    yield "missing_deposit_adjusted_series", {"epoch_ms": _E, "series": good_adj}
    yield (
        "epoch_ms_not_a_list",
        {"epoch_ms": "abc", "series": good_adj, "deposit_adjusted_series": good_adj},
    )
    yield (
        "adjusted_not_a_list",
        {"epoch_ms": _E, "series": good_adj, "deposit_adjusted_series": {"a": 1}},
    )
    yield "adjusted_contains_numeric_strings", _body(_E, ["100.0", "101.0", "102.0"])
    yield "adjusted_contains_bools", _body(_E, [True, False, True])
    yield "epoch_contains_a_string", _body([_E[0], "x", _E[2]], good_adj)
    yield (
        "epoch_values_in_seconds_not_ms",
        _body([1_767_000_000 + i * 86_400 for i in range(3)], good_adj),
    )
    yield "one_epoch_value_below_the_ms_floor", _body([_E[0], 99_999_999_999, _E[2]], good_adj)
    yield "body_is_a_list", [1, 2, 3]
    yield "body_is_a_string", "not json object"


class TestMalformedBodies:
    @pytest.mark.parametrize(
        ("label", "body"), list(_bad_bodies()), ids=[b[0] for b in _bad_bodies()]
    )
    def test_malformed_body_yields_an_error_result_and_never_raises(
        self, client_mod, monkeypatch, label, body
    ):
        res = _fetch(client_mod, _Http([_resp(200, body)]), monkeypatch)
        assert isinstance(res, client_mod.SeriesResult), label
        assert res.returns == {}, label
        assert isinstance(res.error, str) and res.error, label
        assert res.points == 0
        assert res.first_date is None and res.last_date is None

    def test_non_json_200_body_yields_an_error_result(self, client_mod, monkeypatch):
        res = _fetch(client_mod, _Http([_resp(200, None, text="<html>oops</html>")]), monkeypatch)
        assert res.returns == {}
        assert isinstance(res.error, str) and res.error

    def test_malformed_body_values_are_never_echoed_into_the_error(self, client_mod, monkeypatch):
        marker = "SECRET-BODY-VALUE-789"
        res = _fetch(
            client_mod,
            _Http([_resp(200, _body([_E[0], marker, _E[2]], [100.0, 101.0, 102.0]))]),
            monkeypatch,
        )
        assert res.error
        assert marker not in res.error

    def test_empty_arrays_yield_no_returns_without_raising(self, client_mod, monkeypatch):
        res = _fetch(client_mod, _Http([_resp(200, _body([], []))]), monkeypatch)
        assert res.returns == {}

    def test_a_single_point_yields_no_returns_without_raising(self, client_mod, monkeypatch):
        res = _fetch(client_mod, _Http([_resp(200, _body([_E[0]], [100.0]))]), monkeypatch)
        assert res.returns == {}


# ===========================================================================
# Retry policy
# ===========================================================================


def _good_resp():
    return _resp(200, _body([_utc_ms(2026, 2, 2), _utc_ms(2026, 2, 3)], [100.0, 101.0]))


class TestRetryAfterHandling:
    def test_429_with_retry_after_then_success_honours_the_header(self, client_mod, monkeypatch):
        cap = client_mod.RETRY_AFTER_CAP_SECONDS
        retry_after = max(1, int(cap) // 2)
        http = _Http([_resp(429, headers={"Retry-After": str(retry_after)}), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.error is None
        assert len(res.returns) == 1
        assert len(http.calls) == 2
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == 1
        assert min(retry_after, cap) <= sleeps[0] <= cap

    def test_an_oversized_retry_after_is_capped(self, client_mod, monkeypatch):
        cap = client_mod.RETRY_AFTER_CAP_SECONDS
        http = _Http([_resp(429, headers={"Retry-After": str(int(cap) * 1000)}), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.error is None
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(cap)  # capped, not honoured verbatim

    def test_an_unparseable_retry_after_falls_back_to_the_default_backoff(
        self, client_mod, monkeypatch
    ):
        cap = client_mod.RETRY_AFTER_CAP_SECONDS
        http = _Http(
            [_resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), _good_resp()]
        )
        res = _fetch(client_mod, http, monkeypatch)

        assert res.error is None
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == 1
        assert 0 <= sleeps[0] <= cap

    @pytest.mark.parametrize(
        "header",
        ["-5", "-1", "0", "1.5", "", "   ", "abc", "nan", "inf", "-inf"],
        ids=[
            "neg5",
            "neg1",
            "zero",
            "fractional",
            "empty",
            "blank",
            "word",
            "nan",
            "inf",
            "neginf",
        ],
    )
    def test_odd_retry_after_values_never_cause_a_negative_or_oversized_sleep(
        self, client_mod, monkeypatch, header
    ):
        """A hostile/buggy Retry-After must be handled: the fetch recovers, never
        raises (time.sleep(-5) would raise ValueError), and every sleep lands in
        [0, RETRY_AFTER_CAP_SECONDS] -- a negative header is clamped to 0."""
        cap = client_mod.RETRY_AFTER_CAP_SECONDS
        http = _Http([_resp(429, headers={"Retry-After": header}), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.error is None, header
        assert len(res.returns) == 1
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) <= 1
        assert all(0 <= sl <= cap for sl in sleeps), sleeps

    def test_429_without_a_retry_after_header_still_retries_with_a_bounded_sleep(
        self, client_mod, monkeypatch
    ):
        cap = client_mod.RETRY_AFTER_CAP_SECONDS
        http = _Http([_resp(429), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.error is None
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == 1 and 0 <= sleeps[0] <= cap

    def test_persistent_429_exhausts_the_bounded_retry_and_reports_the_status(
        self, client_mod, monkeypatch
    ):
        http = _Http([_resp(429, headers={"Retry-After": "1"})])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.returns == {}
        assert res.error and "429" in res.error
        assert len(http.calls) == client_mod.MAX_ATTEMPTS
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == client_mod.MAX_ATTEMPTS - 1, "sleep only BETWEEN attempts"
        assert all(0 <= s <= client_mod.RETRY_AFTER_CAP_SECONDS for s in sleeps)


class TestServerErrorsAndTransportFailures:
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_then_success_is_retried(self, client_mod, monkeypatch, status):
        http = _Http([_resp(status), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)
        assert res.error is None
        assert len(http.calls) == 2

    @pytest.mark.parametrize("status", [500, 503])
    def test_persistent_5xx_exhausts_retries_and_reports_the_status(
        self, client_mod, monkeypatch, status
    ):
        http = _Http([_resp(status)])
        res = _fetch(client_mod, http, monkeypatch)

        assert res.returns == {}
        assert res.error and str(status) in res.error
        assert len(http.calls) == client_mod.MAX_ATTEMPTS
        sleeps = client_mod._test_sleeps  # noqa: SLF001
        assert len(sleeps) == client_mod.MAX_ATTEMPTS - 1
        assert all(0 <= s <= client_mod.RETRY_AFTER_CAP_SECONDS for s in sleeps)

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_non_429_4xx_is_not_retried(self, client_mod, monkeypatch, status):
        http = _Http([_resp(status)])
        res = _fetch(client_mod, http, monkeypatch)

        assert len(http.calls) == 1, "a client error will not fix itself -- no retry"
        assert client_mod._test_sleeps == []  # noqa: SLF001
        assert res.returns == {}
        assert res.error and str(status) in res.error

    @pytest.mark.parametrize(
        "exc", [requests.exceptions.Timeout("t/o"), requests.exceptions.ConnectionError("down")]
    )
    def test_transport_errors_are_retried_then_reported_by_type_name(
        self, client_mod, monkeypatch, exc
    ):
        http = _Http([exc])
        res = _fetch(client_mod, http, monkeypatch)

        assert len(http.calls) == client_mod.MAX_ATTEMPTS
        assert len(client_mod._test_sleeps) == client_mod.MAX_ATTEMPTS - 1  # noqa: SLF001
        assert res.returns == {}
        assert res.error and type(exc).__name__ in res.error

    def test_a_transient_timeout_followed_by_success_recovers(self, client_mod, monkeypatch):
        http = _Http([requests.exceptions.Timeout("t/o"), _good_resp()])
        res = _fetch(client_mod, http, monkeypatch)
        assert res.error is None
        assert len(res.returns) == 1

    def test_an_unexpected_exception_from_the_transport_never_escapes(
        self, client_mod, monkeypatch
    ):
        http = _Http([RuntimeError("something odd")])
        res = _fetch(client_mod, http, monkeypatch)
        assert isinstance(res, client_mod.SeriesResult)
        assert res.returns == {}
        assert res.error and "RuntimeError" in res.error


# ===========================================================================
# D-1: error strings and logs carry no bodies, headers, keys, or messages
# ===========================================================================


class TestD1ErrorDisclosure:
    def _assert_clean(self, text: str, *markers: str):
        for marker in (*markers, _SECRET_KEY_ID, _SECRET_TOKEN, "SECRET-TOKEN-XYZ"):
            assert marker not in text, f"{marker!r} leaked into {text!r}"

    @pytest.mark.parametrize("status", [401, 404, 500, 503])
    def test_http_error_strings_never_contain_the_response_body(
        self, client_mod, monkeypatch, caplog, status
    ):
        marker = "SECRET-BODY-TEXT-123"
        resp = _resp(status, {"message": marker}, text=marker)
        with caplog.at_level(logging.DEBUG):
            res = _fetch(client_mod, _Http([resp]), monkeypatch)

        assert res.error and str(status) in res.error
        self._assert_clean(res.error, marker)
        self._assert_clean(caplog.text, marker)

    def test_exception_messages_never_reach_the_error_or_the_logs(
        self, client_mod, monkeypatch, caplog
    ):
        marker = "SECRET-EXC-MESSAGE-456"
        exc = requests.exceptions.ConnectionError(f"{marker} Authorization: {_SECRET_TOKEN}")
        with caplog.at_level(logging.DEBUG):
            res = _fetch(client_mod, _Http([exc]), monkeypatch)

        assert res.error and "ConnectionError" in res.error
        self._assert_clean(res.error, marker)
        self._assert_clean(caplog.text, marker)

    def test_credentials_never_appear_in_logs_on_a_successful_call(
        self, client_mod, monkeypatch, caplog
    ):
        with caplog.at_level(logging.DEBUG):
            _fetch(client_mod, _Http([_good_resp()]), monkeypatch)
        self._assert_clean(caplog.text)

    def test_error_strings_are_short(self, client_mod, monkeypatch):
        res = _fetch(
            client_mod, _Http([_resp(500, {"m": "x" * 5000}, text="x" * 5000)]), monkeypatch
        )
        assert res.error and len(res.error) <= 200


# ===========================================================================
# Property-based invariants (hypothesis)
# ===========================================================================


class TestReturnInvariants:
    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(values=st.lists(st.floats(min_value=1.0, max_value=1e7), min_size=2, max_size=40))
    def test_returns_compound_back_to_the_total_value_ratio(self, client_mod, values):
        """For any strictly positive value series on distinct past days: one
        return per consecutive pair, all finite, and compounding them
        reproduces v_last / v_first (a defining property of simple returns)."""
        start = date(2025, 3, 3)
        epochs = [_et_ms((start + timedelta(days=i)).isoformat(), 16) for i in range(len(values))]
        http = _Http([_resp(200, _body(epochs, values))])
        with patch.object(requests, "get", http):
            res = client_mod.fetch_symphony_daily_returns(_ACCOUNT, _SYMPHONY)

        assert res.error is None
        assert len(res.returns) == len(values) - 1
        assert all(math.isfinite(v) for v in res.returns.values())
        compounded = math.prod(1.0 + r / 100.0 for r in res.returns.values())
        # rel=1e-6: a product of up to 39 ratios each carrying ~1e-16 relative
        # error, then divided back from percent scale -- orders of magnitude of slack.
        assert compounded == pytest.approx(values[-1] / values[0], rel=1e-6)
        assert list(res.returns) == sorted(res.returns), "keys are chronological"
