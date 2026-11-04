"""Shared test helpers for the regime label job tests (not a test module).

Everything here is derived from the captured Alpaca record shape under
tests/fixtures/alpaca/stock_bars_captured/ (see its PROVENANCE.md). No helper
reaches the network: ``AlpacaStub`` stands in for ``requests.get``.
"""

from __future__ import annotations

import copy
import json
import pathlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import market_calendar

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CAPTURED_DIR = REPO_ROOT / "tests" / "fixtures" / "alpaca" / "stock_bars_captured"
DERIVED_DIR = REPO_ROOT / "tests" / "fixtures" / "alpaca" / "stock_bars_derived"
MATH_DIR = REPO_ROOT / "tests" / "fixtures" / "math" / "regime_classifier"

ET = ZoneInfo("America/New_York")

# A job start whose ET date is Tue 2026-11-03 at 23:30, so the target session D is
# Wed 2026-11-04 and the newest completed session at the request end bound is 2026-11-03.
JOB_START = datetime(2026, 11, 4, 4, 30, tzinfo=UTC)
TARGET_D = date(2026, 11, 4)
NEWEST_ELIGIBLE = date(2026, 11, 3)


def load_captured(prefix: str) -> dict:
    path = next(CAPTURED_DIR.glob(f"{prefix}__*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def load_derived(name: str) -> dict:
    return json.loads((DERIVED_DIR / name).read_text(encoding="utf-8"))


def load_golden_returns(name: str) -> list[float]:
    data = json.loads((MATH_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return list(data["inputs"]["returns"])


def sessions_ending(last: date, n: int) -> list[date]:
    """The n NYSE sessions ending on `last` (inclusive), oldest first. Independent
    of the module under test: walks calendar days against market_calendar."""
    out: list[date] = []
    d = last
    while len(out) < n:
        if market_calendar.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def bar_stamp(d: date) -> str:
    """Alpaca stamps a daily bar at 00:00 ET of its session, rendered in UTC."""
    midnight_et = datetime(d.year, d.month, d.day, tzinfo=ET)
    return midnight_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_bar(d: date, close: float) -> dict:
    return {
        "c": close,
        "h": close,
        "l": close,
        "n": 100,
        "o": close,
        "t": bar_stamp(d),
        "v": 1000,
        "vw": close,
    }


def closes_from_returns(returns: list[float], sessions: list[date], base: float = 100.0) -> dict:
    """{date: close} so that close[s_i]/close[s_{i-1}] - 1 == returns[i-1]."""
    assert len(sessions) == len(returns) + 1
    out = {sessions[0]: base}
    for s, r in zip(sessions[1:], returns, strict=True):
        out[s] = out[sessions[sessions.index(s) - 1]] * (1.0 + r)
    return out


class Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status: int = 200, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return copy.deepcopy(self._body)


def page_body(closes: dict, next_page_token: str | None = None) -> dict:
    """closes: {ticker: {date: close}} -> a /v2/stocks/bars body."""
    return {
        "bars": {
            t: [make_bar(d, c) for d, c in sorted(table.items())] for t, table in closes.items()
        },
        "next_page_token": next_page_token,
    }


class AlpacaStub:
    """Stands in for ``requests.get``. Serves bars from `closes` like the real endpoint:
    only the requested symbols, only bars stamped within [start, end], and a symbol with
    no bars is simply absent from the 200 body. `rule(params)` may return a Resp or an
    exception to raise (for a batch-level failure); None means serve normally."""

    def __init__(self, closes: dict, rule=None, *, serve_all: bool = False):
        self.closes = closes
        self.rule = rule
        self.serve_all = (
            serve_all  # ignore start/end (a source that returns bars beyond the request)
        )
        self.calls: list[dict] = []

    def __call__(self, url=None, **kwargs):
        self.calls.append({"url": url, **kwargs})
        params = kwargs.get("params") or {}
        if self.rule is not None:
            out = self.rule(params)
            if isinstance(out, BaseException):
                raise out
            if out is not None:
                return out
        symbols = [s for s in params.get("symbols", "").split(",") if s]
        start = _parse_param(params.get("start"), midnight_et=True)
        end = _parse_param(params.get("end"), midnight_et=False)
        served: dict = {}
        for sym in symbols:
            table = self.closes.get(sym) or {}
            keep = {
                d: c
                for d, c in table.items()
                if self.serve_all
                or ((start is None or bar_dt(d) >= start) and (end is None or bar_dt(d) <= end))
            }
            if keep:
                served[sym] = keep
        return Resp(200, page_body(served))


def bar_dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC)


def _parse_param(value, *, midnight_et: bool):
    if not value:
        return None
    if len(value) == 10:
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def holdings_of(*pairs) -> list[dict]:
    return [{"ticker": t, "allocation": a} for t, a in pairs]


def sample_std(values: list[float]) -> float:
    n = len(values)
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5


def bar_date(t: str) -> date:
    """ET calendar date of a bar stamp (independent of the module under test)."""
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET).date()


def closes_from_body(body: dict) -> dict:
    """{ticker: {date: close}} from a /v2/stocks/bars body."""
    return {sym: {bar_date(b["t"]): b["c"] for b in bars} for sym, bars in body["bars"].items()}


def random_returns(n: int, seed: int, scale: float = 0.01) -> list[float]:
    import random

    rng = random.Random(seed)
    return [rng.uniform(-scale, scale) for _ in range(n)]


def window_candidates(session: date) -> list[date]:
    """The 31 sessions an implementation must consider for D: the 30 strictly before it plus one."""
    last = session - timedelta(days=1)
    while not market_calendar.is_trading_day(last):
        last -= timedelta(days=1)
    return sessions_ending(last, 31)


def oracle_window(holdings, closes, session: date, newest_eligible: date, take: int = 20):
    """Independent reference: [(session, return)] for the `take` most recent computable sessions
    among the 30 before `session`. A session is computable when every holding has a close on it and
    on the session before it, and the session is not later than `newest_eligible`. No default fill."""
    sess = window_candidates(session)
    out = []
    for i in range(1, len(sess)):
        s, prev = sess[i], sess[i - 1]
        if s > newest_eligible:
            continue
        if all(
            s in closes.get(h["ticker"], {}) and prev in closes.get(h["ticker"], {})
            for h in holdings
        ):
            out.append(
                (
                    s,
                    sum(
                        h["allocation"] * (closes[h["ticker"]][s] / closes[h["ticker"]][prev] - 1.0)
                        for h in holdings
                    ),
                )
            )
    return out[-take:]
