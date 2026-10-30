"""TEST-ONLY HTTP router for the tenure review Composer reads and runner suites.

Every request the code under test makes through ``requests`` is answered here from the
captured Composer records under ``tests/fixtures/composer/tenure-review/`` (see the README
there for provenance). Nothing reaches the network: ``requests.get``, ``requests.post`` and
every other verb helper are replaced, together with ``requests.sessions.Session.request``
and ``time.sleep``. A request that is not one of the five read or backtest shapes is
recorded as a violation and answered 404, so a hidden write path shows up in the request
ledger instead of in a swallowed exception.

Fixtures this module builds replies from are captured producer output. Derived forms are
used and labelled where they are made: a record with an array emptied or a key removed (the
empty-result and missing-field edges of a captured record), the holdings record with its
``by_symphony`` map emptied or filtered to a subset of its own symphonies, and a captured
error body with a marker string appended.

Not imported by production code.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import requests

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "composer" / "tenure-review"
STRATEGY_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_builder"

BASE_URL = "https://api.composer.trade/api/v0.1"

# Invented account ids in the real uuid shape; the captured records carry a redaction
# placeholder, and the router answers by path shape whatever the account id is.
ACCOUNT_ID = "6f1c9d2e-4b7a-4e53-9a08-3c5d7e1b2a94"
SECOND_ACCOUNT_ID = "b2e8a4c0-91d3-47f6-8e5a-0d7c3f6a1b58"

# The four live symphonies the capture holds a complete series for.
OLDEST_ID = "n2ooAZTvBRN6ZzpMmWmU"  # 570 points, invested 2024-07-12
MID_ID = "8FAXAnQmYi1INDubazeC"  # 233 points, series and deposit-adjusted differ on every point
BACKTEST_ID = "nOyb55RMGVCKPiYXv7TI"  # 148 points; subject of the definition and backtest records
YOUNGEST_ID = "q7WQHwLzt49cMgFBzHsX"  # 28 points, invested 2026-09-10
CAPTURED_SERIES_IDS = (OLDEST_ID, MID_ID, BACKTEST_ID, YOUNGEST_ID)

SERVICE_KEY_ID = "test-key-id-7c41e0aa"
SERVICE_SECRET = "test-secret-5b93d2f1-not-a-real-credential"

_STATS_RE = re.compile(r"^/portfolio/accounts/(?P<account>[^/]+)/symphony-stats-meta$")
_SERIES_RE = re.compile(r"^/portfolio/accounts/(?P<account>[^/]+)/symphonies/(?P<sid>[^/]+)$")
_HOLDINGS_RE = re.compile(r"^/portfolio/accounts/(?P<account>[^/]+)/symphony-historical-holdings$")
_SCORE_RE = re.compile(r"^/symphonies/(?P<sid>[^/]+)/score$")
_BACKTEST_RE = re.compile(r"^/backtest$")

# The four GET reads of the portfolio client: statistics, value series, holdings and the
# definition ("score") read.
READ_KINDS = ("statistics", "series", "holdings", "score")
# Every kind of request a review run may make: the four reads and the backtest POST.
COMPOSER_KINDS = (*READ_KINDS, "backtest")


# ---------------------------------------------------------------------------
# Captured records
# ---------------------------------------------------------------------------


def load_record(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def record_body(name: str):
    """A deep copy of a record's response body (text for a non-JSON body)."""
    record = load_record(name)
    if "response_body" in record:
        return copy.deepcopy(record["response_body"])
    return record["response_body_text"]


def record_status(name: str) -> int:
    return load_record(name)["response_status"]


def synthetic_non_json_text() -> str:
    """The one hand-written fixture: an HTML body served with status 200 (see the README)."""
    return (FIXTURE_DIR / "synthetic_non_json_200.txt").read_text(encoding="utf-8")


def marketcap_422_record() -> dict:
    return json.loads(
        (STRATEGY_FIXTURE_DIR / "wt_marketcap_deprecated_envelope.json").read_text(encoding="utf-8")
    )


def series_record_name(symphony_id: str) -> str:
    return f"case2_series_{symphony_id}.json"


def statistics_body() -> dict:
    return record_body("case1_statistics_roth.json")


def statistics_ids() -> list[str]:
    return [entry["id"] for entry in statistics_body()["symphonies"]]


def holdings_recent_body() -> dict:
    return record_body("case3_holdings_recent_2026-09-20_2026-10-19.json")


def holdings_older_body() -> dict:
    return record_body("case3_holdings_older_2026-04-06_2026-05-05.json")


def holdings_emptied_body() -> dict:
    """The holdings record with its ``by_symphony`` map emptied (empty-result edge)."""
    body = holdings_recent_body()
    body["by_symphony"] = {}
    return body


def holdings_subset_body(symphony_ids) -> dict:
    """The recent holdings record keeping only the listed symphonies (their own entries)."""
    body = holdings_recent_body()
    keep = set(symphony_ids)
    body["by_symphony"] = {sid: days for sid, days in body["by_symphony"].items() if sid in keep}
    return body


def backtest_success_body() -> dict:
    return record_body("case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json")


def definition_body() -> dict:
    return record_body("case4_definition_nOyb55RMGVCKPiYXv7TI.json")


def error_body_for(status: int):
    """A captured Composer error body to serve with ``status`` (the body never decides
    the failure class, the status does)."""
    if status == 404:
        return record_body("case7a_series_nonexistent_account_404.json")
    if status >= 500:
        return record_body("case7c_backtest_unknown_ticker_500.json")
    return record_body("case7b_series_invalid_credentials_401.json")


# ---------------------------------------------------------------------------
# Independent oracles over the captured series and holdings
# ---------------------------------------------------------------------------


def utc_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, UTC).date().isoformat()


def series_points(symphony_id: str) -> dict[str, float]:
    """ISO date -> deposit-adjusted value of a captured series (never ``series``)."""
    body = record_body(series_record_name(symphony_id))
    return {
        utc_date(ms): value
        for ms, value in zip(body["epoch_ms"], body["deposit_adjusted_series"], strict=True)
    }


def series_dates(symphony_id: str) -> list[str]:
    return [utc_date(ms) for ms in record_body(series_record_name(symphony_id))["epoch_ms"]]


def holdings_days(body: dict) -> dict[str, dict[str, dict[str, float]]]:
    """date -> symphony id -> ticker -> value, from a holdings response body."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for sid, by_day in body["by_symphony"].items():
        for day, rows in by_day.items():
            out.setdefault(day, {})[sid] = {row["symbol"]: row["value"] for row in rows}
    return out


def day_integer_to_iso(day_int: int | str) -> str:
    return (date(1970, 1, 1) + timedelta(days=int(day_int))).isoformat()


# ---------------------------------------------------------------------------
# Replies and the request ledger
# ---------------------------------------------------------------------------


@dataclass
class Reply:
    """What the router answers: a status and body, or an exception to raise."""

    status: int = 200
    body: object = None  # dict or list -> JSON; str -> sent verbatim
    content_type: str | None = None
    headers: dict = field(default_factory=dict)
    raises: BaseException | None = None


def reply_from_record(name: str, *, status: int | None = None) -> Reply:
    """A reply carrying a captured record's body; its recorded status unless overridden."""
    return Reply(status=record_status(name) if status is None else status, body=record_body(name))


def error_reply(status: int) -> Reply:
    return Reply(status=status, body=error_body_for(status))


def marked_error_reply(status: int, marker: str) -> Reply:
    """A captured error reply for ``status`` whose body also carries ``marker`` near its start
    (a derived form, labelled here): the captured body with the marker put in front of its
    first title, or in front of a text body. A test then proves that no log line repeats a
    response body, even a body cut to its first 200 characters."""
    body = error_body_for(status)
    if isinstance(body, str):
        body = f"<!-- {marker} -->{body}"
    elif isinstance(body, dict) and isinstance(body.get("errors"), list) and body["errors"]:
        first = body["errors"][0]
        first["title"] = f"{marker} {first.get('title', '')}".strip()
    else:
        body = {"marker": marker, **(body if isinstance(body, dict) else {})}
    return Reply(status=status, body=body)


def redirect_reply(status: int = 302) -> Reply:
    """A redirect answer: no captured body, the Location header is a placeholder host."""
    return Reply(status=status, body="", headers={"Location": "https://redirect.invalid/elsewhere"})


def raising(exc: BaseException) -> Reply:
    return Reply(raises=exc)


def non_json_200() -> Reply:
    return Reply(status=200, body=synthetic_non_json_text(), content_type="text/html")


@dataclass
class Request:
    seq: int
    method: str
    url: str
    path: str
    query: dict
    kind: str
    match: dict
    kwargs: dict

    @property
    def headers(self) -> dict:
        return self.kwargs.get("headers") or {}

    @property
    def timeout(self):
        return self.kwargs.get("timeout")

    def body_text(self) -> str | None:
        data = self.kwargs.get("data")
        if isinstance(data, bytes | bytearray):
            return bytes(data).decode("utf-8")
        return data

    def body_json(self) -> dict:
        """The posted body as a dict, whether sent as ``json=`` or as ``data=``."""
        data = self.body_text()
        if data is not None:
            return json.loads(data)
        return self.kwargs.get("json")


def _build_response(reply: Reply, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = reply.status
    response.url = url
    response.encoding = "utf-8"
    if isinstance(reply.body, str):
        payload = reply.body.encode("utf-8")
        content_type = reply.content_type or "text/html"
    elif reply.body is None:
        payload = b""
        content_type = reply.content_type or "application/json"
    else:
        payload = json.dumps(reply.body).encode("utf-8")
        content_type = reply.content_type or "application/json; charset=utf-8"
    response._content = payload
    response.headers["Content-Type"] = content_type
    for key, value in reply.headers.items():
        response.headers[key] = value
    return response


class ComposerRouter:
    """Serves captured Composer responses and records every request and sleep."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.events: list[tuple] = []
        self.violations: list[Request] = []
        self.sleeps: list[float] = []

        self.statistics_default = reply_from_record("case1_statistics_roth.json")
        self.statistics_by_account: dict[str, Reply] = {}

        self.series_default = reply_from_record("case7a_series_nonexistent_symphony_200_empty.json")
        self.series_by_symphony: dict[str, Reply] = {
            sid: reply_from_record(series_record_name(sid)) for sid in CAPTURED_SERIES_IDS
        }

        self.definition_default = error_reply(404)
        self.definition_by_symphony: dict[str, Reply] = {
            BACKTEST_ID: reply_from_record("case4_definition_nOyb55RMGVCKPiYXv7TI.json")
        }

        self.backtest_handler: Callable[[Request, int], Reply] = lambda req, i: reply_from_record(
            "case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json"
        )
        # Called with (request, index of this holdings request for its account).
        self.holdings_handler: Callable[[Request, int], Reply] = self._default_holdings

    # ---- defaults -------------------------------------------------------

    @staticmethod
    def _default_holdings(request: Request, index: int) -> Reply:
        if index == 0:
            return Reply(body=holdings_recent_body())
        return Reply(body=holdings_emptied_body())

    # ---- ledger views ---------------------------------------------------

    def of_kind(self, kind: str) -> list[Request]:
        return [r for r in self.requests if r.kind == kind]

    def series_requests(self, symphony_id: str) -> list[Request]:
        return [r for r in self.of_kind("series") if r.match["sid"] == symphony_id]

    def holdings_requests(self, account: str | None = None) -> list[Request]:
        return [
            r for r in self.of_kind("holdings") if account is None or r.match["account"] == account
        ]

    def set_series(self, symphony_id: str, reply: Reply) -> None:
        self.series_by_symphony[symphony_id] = reply

    def set_all_holdings(self, reply: Reply) -> None:
        self.holdings_handler = lambda req, i: reply

    def set_backtest(self, reply: Reply) -> None:
        self.backtest_handler = lambda req, i: reply

    # ---- dispatch -------------------------------------------------------

    def _classify(self, method: str, url: str, kwargs: dict) -> tuple[str, str, dict, dict]:
        split = urlsplit(url)
        query = dict(parse_qsl(split.query))
        for key, value in (kwargs.get("params") or {}).items():
            query[key] = str(value)
        full = f"{split.scheme}://{split.netloc}{split.path}"
        if not full.startswith(BASE_URL):
            return "foreign", split.path, query, {}
        path = full[len(BASE_URL) :]
        if method == "GET":
            for kind, pattern in (
                ("statistics", _STATS_RE),
                ("series", _SERIES_RE),
                ("holdings", _HOLDINGS_RE),
                ("score", _SCORE_RE),
            ):
                found = pattern.match(path)
                if found:
                    return kind, path, query, found.groupdict()
        if method == "POST" and _BACKTEST_RE.match(path):
            return "backtest", path, query, {}
        return "unknown", path, query, {}

    def _reply_for(self, request: Request) -> Reply:
        kind, match = request.kind, request.match
        if kind == "statistics":
            return self.statistics_by_account.get(match["account"], self.statistics_default)
        if kind == "series":
            return self.series_by_symphony.get(match["sid"], self.series_default)
        if kind == "holdings":
            index = len(self.holdings_requests(match["account"])) - 1
            return self.holdings_handler(request, index)
        if kind == "score":
            return self.definition_by_symphony.get(match["sid"], self.definition_default)
        if kind == "backtest":
            return self.backtest_handler(request, len(self.of_kind("backtest")) - 1)
        return error_reply(404)

    def dispatch(self, method: str, url: str, kwargs: dict) -> requests.Response:
        kind, path, query, match = self._classify(method, url, kwargs)
        request = Request(len(self.requests), method, url, path, query, kind, match, dict(kwargs))
        self.requests.append(request)
        self.events.append(("request", request))
        if kind in ("foreign", "unknown"):
            self.violations.append(request)
        reply = self._reply_for(request)
        if reply.raises is not None:
            raise reply.raises
        return _build_response(reply, url)

    def sleep(self, seconds) -> None:
        self.sleeps.append(seconds)
        self.events.append(("sleep", seconds))

    # ---- installation ---------------------------------------------------

    def install(self, monkeypatch) -> ComposerRouter:
        """Replace every outbound path of ``requests`` and ``time.sleep`` for one test."""
        import alpha_bot_execution  # noqa: PLC0415

        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_KEY_ID", SERVICE_KEY_ID)
        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_SECRET", SERVICE_SECRET)

        def verb(method):
            def call(url, **kwargs):
                return self.dispatch(method, url, kwargs)

            return call

        for name in ("get", "post", "put", "patch", "delete", "head", "options"):
            monkeypatch.setattr(requests, name, verb(name.upper()))

        def generic_request(method, url, **kwargs):
            return self.dispatch(str(method).upper(), url, kwargs)

        monkeypatch.setattr(requests, "request", generic_request)

        def session_request(session, method, url, **kwargs):
            return self.dispatch(str(method).upper(), url, kwargs)

        monkeypatch.setattr(requests.sessions.Session, "request", session_request)
        monkeypatch.setattr(time, "sleep", self.sleep)
        return self


# ---------------------------------------------------------------------------
# Assertions shared by the client and runner suites
# ---------------------------------------------------------------------------


def assert_reads_paced(router: ComposerRouter, spacing: float) -> None:
    """Every statistics, series, holdings and definition request, whether it was answered or
    raised, is immediately followed by a sleep of ``spacing`` seconds, before the next request
    of any kind."""
    events = router.events
    for i, event in enumerate(events):
        if event[0] == "request" and event[1].kind in READ_KINDS:
            following = events[i + 1] if i + 1 < len(events) else None
            assert following == ("sleep", spacing), (
                f"{event[1].method} {event[1].path} is not followed by a {spacing} second sleep "
                f"(next event: {following!r})"
            )


def assert_requests_spaced(
    router: ComposerRouter, spacing: float, *, tolerance: float = 0.01
) -> None:
    """Between any two consecutive Composer requests of the run, whatever their kinds, the
    sleeps recorded add up to at least ``spacing`` seconds (a tolerance covers a pause
    computed from a clock reading)."""
    pending: float | None = None
    for event in router.events:
        if event[0] == "sleep":
            if pending is not None:
                pending += float(event[1])
            continue
        request = event[1]
        if request.kind not in COMPOSER_KINDS:
            continue
        if pending is not None:
            assert pending >= spacing - tolerance, (
                f"{request.method} {request.path} follows the previous request after only "
                f"{pending} seconds of sleep (needs {spacing})"
            )
        pending = 0.0
