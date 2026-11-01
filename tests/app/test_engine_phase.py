"""RED tests: the scheduled 09:30 start and the rebalance blackout read as scheduled.

`/api/state` carries ``engine_phase`` ("closed", "blackout", "opening" or "running") in every
branch, computed once per request from the request's own instant, the market state and the
bot_state's ``last_successful_cycle_at``. The header (``updateEngineStatus`` in
static/chrome.js and the server render of templates/_chrome.html) reads "Rebalance pause" or
"Opening" for the two scheduled windows, with a tooltip that says the pause is scheduled,
instead of "Stale".

Contract pinned here:
  - ``app._engine_phase(now_et, market_state, last_cycle_at) -> str`` is pure: "closed" unless
    ``market_state == "open"``; "blackout" from ``market_calendar.rebalance_blackout_start``
    (inclusive) to the session close; "opening" from 09:30:00 for ``_OPENING_PHASE_MINUTES``
    (5) while ``last_cycle_at`` is absent or earlier than that day's 09:30 ET; else "running".
  - ``updateEngineStatus`` orders its labels Closed, then Rebalance pause / Opening, then
    Stale, then Live.

Session times come from the golden calendar fixture
(tests/fixtures/math/holiday_calendar/session_times_known_dates.json), so the half-days are
covered with the close the calendar gives. Stamps are built in the captured value's format
from the frozen instant (tests/fixtures/refresh_timing/02-c5/PROVENANCE.md). Expected phases and
labels are derived from the calendar and the case table, never copied from a run.

Each unit test sweeps a whole family of cases and reports every wrong one, because every test
pays for a rebuilt test database.
"""

from __future__ import annotations

import copy
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module
import market_calendar
from tests.app.test_held_basis_route_convergence import _frozen_datetime_class

_ET = ZoneInfo("America/New_York")
_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO / "tests" / "fixtures"
_C5_DIR = _FIXTURES / "refresh_timing" / "02-c5"
_CHROME_JS = _REPO / "static" / "chrome.js"

_STATE_ENTRY: dict = json.loads((_C5_DIR / "symphony_state_entry.json").read_text("utf-8"))
_SYMPHONY_ID: str = next(iter(_STATE_ENTRY))
_CAPTURED_STAMP: str = json.loads((_C5_DIR / "cycle_marker.json").read_text("utf-8"))[
    "last_successful_cycle_at"
]
_GOLDEN = json.loads(
    (_FIXTURES / "math" / "holiday_calendar" / "session_times_known_dates.json").read_text("utf-8")
)

_PHASES = {"closed", "blackout", "opening", "running"}
_BLACKOUT_TITLE = "Scheduled pause before the close; stops resume at the close."
_OPENING_TITLE = "Scheduled session start; the first cycle is running."
_STALE_TITLE = "Positions may not be actively guarded."
_DIM = "var(--studio-ink-dim)"

_SESSIONS = [date.fromisoformat(case["date"]) for case in _GOLDEN["cases"]]
_TRADING_DAY = date(2026, 10, 22)  # a Thursday, EDT
_SATURDAY = date(2026, 10, 24)


def _at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=_ET)


def _stamp(moment: datetime) -> str:
    """A `last_successful_cycle_at` in the captured value's format for `moment`."""
    captured = datetime.fromisoformat(_CAPTURED_STAMP)
    return moment.replace(microsecond=captured.microsecond).isoformat()


def _phase(now_et: datetime, market_state: str, last_cycle_at):
    fn = getattr(app_module, "_engine_phase", None)
    if fn is None:
        pytest.fail(
            "app._engine_phase(now_et, market_state, last_cycle_at) is missing: it must return "
            "'closed', 'blackout', 'opening' or 'running' from the request's instant, the "
            "market state and the last successful cycle stamp."
        )
    return fn(now_et, market_state, last_cycle_at)


def _close_of(day: date) -> datetime:
    close = market_calendar.session_close(day)
    return _at(day, close.hour, close.minute)


def _wrong(cases: list[tuple[str, object, object]]) -> list[str]:
    return [f"{label}: expected {want!r}, got {got!r}" for label, want, got in cases if want != got]


class TestStampBuilderMatchesTheCapturedFormat:
    def test_built_stamp_has_the_captured_values_shape(self):
        shape = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}[+-]\d\d:\d\d")
        built = _stamp(_at(_TRADING_DAY, 15, 52, 1))
        assert shape.fullmatch(_CAPTURED_STAMP)
        assert shape.fullmatch(built)
        assert datetime.fromisoformat(built).utcoffset() == timedelta(hours=-4)
        assert datetime.fromisoformat(_stamp(_at(date(2024, 12, 24), 12, 52, 1))).utcoffset() == (
            timedelta(hours=-5)
        )


class TestEnginePhaseFunction:
    def test_phase_is_closed_unless_the_market_is_open(self):
        cases = []
        probes = {
            "blackout minute": _at(_TRADING_DAY, 15, 56),
            "opening minute": _at(_TRADING_DAY, 9, 31),
            "midday": _at(_TRADING_DAY, 11, 0),
        }
        for state in ("pre_market", "closed_frozen", "closed", "", "unknown"):
            for label, moment in probes.items():
                cases.append(
                    (f"{state!r} at {label}", "closed", _phase(moment, state, _stamp(moment)))
                )
        assert not _wrong(cases), "\n".join(_wrong(cases))

    def test_blackout_runs_from_seven_minutes_before_each_sessions_own_close(self):
        cases = []
        for day in _SESSIONS:
            close = _close_of(day)
            start = close - timedelta(minutes=market_calendar.REBALANCE_BLACKOUT_MINUTES)
            fresh = _stamp(start - timedelta(minutes=2))
            old = _stamp(start - timedelta(hours=1))
            for label, moment, want in (
                ("one second before the blackout", start - timedelta(seconds=1), "running"),
                ("first second of the blackout", start, "blackout"),
                ("mid blackout", start + timedelta(minutes=3), "blackout"),
                ("last second before the close", close - timedelta(seconds=1), "blackout"),
            ):
                cases.append((f"{day} {label}", want, _phase(moment, "open", fresh)))
            cases.append(
                (f"{day} blackout with a stamp an hour old", "blackout", _phase(start, "open", old))
            )
            cases.append((f"{day} blackout with no stamp", "blackout", _phase(start, "open", None)))
        assert not _wrong(cases), "\n".join(_wrong(cases))

    def test_opening_lasts_five_minutes_and_ends_with_the_first_cycle(self):
        cases = []
        for day in _SESSIONS:
            opened = _at(day, 9, 30)
            prior_session = _stamp(opened - timedelta(days=1, hours=-1))
            today_first = _stamp(opened + timedelta(seconds=5))
            early_closed_minute = _stamp(opened - timedelta(seconds=1))
            same_instant_other_offset = (
                datetime.fromisoformat(today_first).astimezone(UTC).isoformat()
            )
            for label, moment, stamp, want in (
                ("09:30:00 with no stamp", opened, None, "opening"),
                ("09:30:00 with an empty stamp", opened, "", "opening"),
                (
                    "09:30:20 with the prior session's stamp",
                    opened + timedelta(seconds=20),
                    prior_session,
                    "opening",
                ),
                (
                    "09:34:59 with the prior session's stamp",
                    opened + timedelta(minutes=4, seconds=59),
                    prior_session,
                    "opening",
                ),
                (
                    "09:35:01 with the prior session's stamp",
                    opened + timedelta(minutes=5, seconds=1),
                    prior_session,
                    "running",
                ),
                (
                    "09:36 with no cycle today",
                    opened + timedelta(minutes=6),
                    prior_session,
                    "running",
                ),
                (
                    "09:31 after today's first cycle",
                    opened + timedelta(minutes=1),
                    today_first,
                    "running",
                ),
                (
                    "09:31 with a stamp from 09:29:59",
                    opened + timedelta(minutes=1),
                    early_closed_minute,
                    "opening",
                ),
                (
                    "09:31, today's first cycle stamped in UTC",
                    opened + timedelta(minutes=1),
                    same_instant_other_offset,
                    "running",
                ),
            ):
                cases.append((f"{day} {label}", want, _phase(moment, "open", stamp)))
        assert not _wrong(cases), "\n".join(_wrong(cases))

    def test_opening_window_is_the_named_five_minutes(self):
        assert getattr(app_module, "_OPENING_PHASE_MINUTES", None) == 5

    @pytest.mark.parametrize("moment_hour_minute", [(9, 31), (11, 0)])
    def test_a_malformed_stamp_never_raises_and_still_names_a_phase(self, moment_hour_minute):
        moment = _at(_TRADING_DAY, *moment_hour_minute)
        failures = []
        for bad in ("not a time", "2026-13-45T99:99:99", 12345, 0, {}, [], True):
            try:
                got = _phase(moment, "open", bad)
            except Exception as exc:  # noqa: BLE001 - the case is "never raises"
                failures.append(f"{bad!r} raised {type(exc).__name__}")
                continue
            if got not in _PHASES:
                failures.append(f"{bad!r} gave {got!r}")
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# /api/state and the server-rendered header
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed(stamp: str | None) -> None:
    state: dict = {_SYMPHONY_ID: copy.deepcopy(_STATE_ENTRY[_SYMPHONY_ID])}
    if stamp is not None:
        state["last_successful_cycle_at"] = stamp
    database_module.save_state(state)


def _freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(moment))


_CAPTURED = "captured"
_FIRST_DAY = _TRADING_DAY
_HALF_DAY = date(2024, 12, 24)

# (id, instant, stamp, expected phase)
_STATE_CASES = [
    pytest.param(
        _at(_FIRST_DAY, 15, 56), _stamp(_at(_FIRST_DAY, 15, 52, 1)), "blackout", id="blackout-1556"
    ),
    pytest.param(
        _at(_FIRST_DAY, 15, 53),
        _stamp(_at(_FIRST_DAY, 15, 52, 1)),
        "blackout",
        id="blackout-first-second",
    ),
    pytest.param(
        _at(_FIRST_DAY, 15, 52, 30),
        _stamp(_at(_FIRST_DAY, 15, 52, 1)),
        "running",
        id="running-before-the-blackout",
    ),
    pytest.param(
        _at(_FIRST_DAY, 9, 30, 20), _CAPTURED, "opening", id="opening-with-the-prior-sessions-stamp"
    ),
    pytest.param(_at(_FIRST_DAY, 9, 36), _CAPTURED, "running", id="running-no-cycle-today-0936"),
    pytest.param(
        _at(_FIRST_DAY, 11, 0),
        _stamp(_at(_FIRST_DAY, 10, 57, 1)),
        "running",
        id="running-midday-three-minutes-old",
    ),
    pytest.param(
        _at(_FIRST_DAY, 20, 30), _stamp(_at(_FIRST_DAY, 16, 0, 1)), "closed", id="closed-evening"
    ),
    pytest.param(
        _at(_SATURDAY, 11, 0), _stamp(_at(_FIRST_DAY, 16, 0, 1)), "closed", id="closed-weekend"
    ),
    pytest.param(
        _at(_HALF_DAY, 12, 56),
        _stamp(_at(_HALF_DAY, 12, 52, 1)),
        "blackout",
        id="blackout-on-the-half-day",
    ),
    pytest.param(
        _at(_HALF_DAY, 12, 52, 30),
        _stamp(_at(_HALF_DAY, 12, 52, 1)),
        "running",
        id="running-before-the-half-day-blackout",
    ),
]


class TestApiStateCarriesTheEnginePhase:
    @pytest.mark.parametrize(("moment", "stamp", "expected"), _STATE_CASES)
    def test_live_branch_phase(self, client, monkeypatch, moment, stamp, expected):
        _seed(_CAPTURED_STAMP if stamp == _CAPTURED else stamp)
        _freeze(monkeypatch, moment)

        resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        assert "engine_phase" in body, "/api/state must carry engine_phase on every branch"
        assert body["engine_phase"] == expected, (
            f"at {moment.isoformat()} with the last cycle stamped {stamp}: expected "
            f"{expected!r}, got {body['engine_phase']!r}"
        )

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            pytest.param(_at(_FIRST_DAY, 9, 31), "opening", id="opening-with-no-state"),
            pytest.param(_at(_FIRST_DAY, 11, 0), "running", id="running-with-no-state"),
            pytest.param(_at(_FIRST_DAY, 15, 56), "blackout", id="blackout-with-no-state"),
            pytest.param(_at(_FIRST_DAY, 20, 30), "closed", id="closed-with-no-state"),
        ],
    )
    def test_waiting_branch_carries_the_phase(self, client, monkeypatch, moment, expected):
        _freeze(monkeypatch, moment)

        resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("status") == "waiting", "no bot_state: the waiting branch must be served"
        assert body.get("engine_phase") == expected

    def test_frozen_branch_carries_the_phase(self, client, monkeypatch):
        entry = copy.deepcopy(_STATE_ENTRY[_SYMPHONY_ID])
        state = {
            "last_successful_cycle_at": _stamp(_at(_FIRST_DAY, 16, 0, 1)),
            _SYMPHONY_ID: entry,
            "last_market_close_snapshot": {
                "captured_at_et": "16:00:00 ET",
                "data_as_of": "16:00 ET",
                "trading_day": _FIRST_DAY.isoformat(),
                "accounts_map": {entry.get("account", "ACC1"): [dict(entry, id=_SYMPHONY_ID)]},
            },
        }
        database_module.save_state(state)
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen")
        _freeze(monkeypatch, _at(_FIRST_DAY, 20, 30))

        resp = client.get("/api/state")

        assert resp.status_code == 200
        assert resp.get_json().get("engine_phase") == "closed"

    def test_two_polls_of_the_same_moment_agree(self, client, monkeypatch):
        _seed(_stamp(_at(_FIRST_DAY, 15, 52, 1)))
        _freeze(monkeypatch, _at(_FIRST_DAY, 15, 56))

        first = client.get("/api/state").get_json().get("engine_phase")
        second = client.get("/api/state").get_json().get("engine_phase")

        assert first == second == "blackout"


_LABEL_RE = re.compile(r'id="engine-status-label"[^>]*>([^<]*)<')
_WRAPPER_RE = re.compile(r'<span id="engine-status"[^>]*>')
_TITLE_RE = re.compile(r'title="([^"]*)"')


def _rendered_header(html_text: str) -> tuple[str, str]:
    label = _LABEL_RE.search(html_text)
    wrapper = _WRAPPER_RE.search(html_text)
    assert label is not None, 'id="engine-status-label" not found in the rendered page'
    assert wrapper is not None, 'id="engine-status" not found in the rendered page'
    title = _TITLE_RE.search(wrapper.group(0))
    return label.group(1).strip(), html.unescape(title.group(1)) if title else ""


class TestServerRenderedHeader:
    @pytest.mark.parametrize(
        ("moment", "stamp", "label", "title"),
        [
            pytest.param(
                _at(_FIRST_DAY, 15, 56),
                _stamp(_at(_FIRST_DAY, 15, 52, 1)),
                "Rebalance pause",
                _BLACKOUT_TITLE,
                id="blackout",
            ),
            pytest.param(
                _at(_FIRST_DAY, 9, 31), _CAPTURED, "Opening", _OPENING_TITLE, id="opening"
            ),
            pytest.param(
                _at(_FIRST_DAY, 11, 0),
                _stamp(_at(_FIRST_DAY, 10, 59, 1)),
                "Live",
                "",
                id="live-midday",
            ),
            pytest.param(
                _at(_SATURDAY, 11, 0), _stamp(_at(_FIRST_DAY, 16, 0, 1)), "Closed", "", id="closed"
            ),
        ],
    )
    def test_dashboard_root_renders_the_phase_label_and_tooltip(
        self, client, monkeypatch, moment, stamp, label, title
    ):
        _seed(_CAPTURED_STAMP if stamp == _CAPTURED else stamp)
        _freeze(monkeypatch, moment)

        resp = client.get("/")

        assert resp.status_code == 200
        got_label, got_title = _rendered_header(resp.data.decode("utf-8"))
        assert got_label == label, (
            f"the server-rendered header at {moment.isoformat()} must read {label!r}; got {got_label!r}"
        )
        assert got_title == title

    def test_a_page_that_does_not_pass_the_phase_renders_as_before(self, client, monkeypatch):
        _freeze(monkeypatch, _at(_FIRST_DAY, 15, 56))

        resp = client.get("/history")

        assert resp.status_code == 200
        got_label, got_title = _rendered_header(resp.data.decode("utf-8"))
        assert got_label == "Live", "only pages that pass engine_phase show the scheduled labels"
        assert got_title == ""


# ---------------------------------------------------------------------------
# updateEngineStatus under node
# ---------------------------------------------------------------------------


def _run_header(sequences: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Run the real static/chrome.js under node, one fresh DOM stub per named sequence, one
    ``updateChromeTicker(body, receipt)`` call per payload. A payload's `__ageMs` becomes
    `last_successful_cycle_at` that many milliseconds before the server time the receipt carries
    (the label judges the cycle's age on the server's clock, never the browser's). Returns, per
    sequence, the state after each call."""
    assert shutil.which("node") is not None, "node is required to run static/chrome.js"
    # The scripts are read from their paths inside node: chrome.js is too large to travel on a
    # command line (Windows refuses a long one).
    chrome_path = json.dumps(str(_CHROME_JS))
    guard_path = json.dumps(str(_CHROME_JS.with_name("request_guard.js")))
    harness = f"""
    var vm = require('vm');
    var fs = require('fs');
    var __sequences = {json.dumps(sequences)};
    var __out = {{}};
    Object.keys(__sequences).forEach(function (name) {{
        var els = {{}};
        function el(extra) {{ return Object.assign({{ style: {{}}, textContent: '', title: '' }}, extra || {{}}); }}
        var label = el();
        Object.defineProperty(label, 'innerHTML', {{
            set: function () {{ throw new Error('innerHTML must not be used'); }}
        }});
        var dot = el(), wrap = el(), countdown = el();
        els['engine-status-dot'] = dot; els['engine-status-label'] = label;
        els['engine-status'] = wrap; els['next-run-countdown'] = countdown;
        var serverNow = Date.now();
        var perf = 5000;
        var rootClasses = {{}};
        var sandbox = {{
            document: {{
                documentElement: {{
                    classList: {{
                        toggle: function (name, force) {{
                            if (force === undefined) force = !rootClasses[name];
                            if (force) rootClasses[name] = true; else delete rootClasses[name];
                            return force;
                        }}
                    }}
                }},
                getElementById: function (id) {{ return els[id] || null; }},
                querySelector: function () {{ return null; }},
                querySelectorAll: function () {{ return []; }},
                addEventListener: function () {{}}
            }},
            setInterval: function () {{ return 1; }}, setTimeout: function () {{ return 1; }},
            clearTimeout: function () {{}}, clearInterval: function () {{}},
            fetch: function () {{ return new Promise(function () {{}}); }},
            performance: {{ now: function () {{ return perf; }} }},
            navigator: {{}}, console: console, Date: Date
        }};
        sandbox.window = sandbox;
        var ctx = vm.createContext(sandbox);
        vm.runInContext(fs.readFileSync({guard_path}, 'utf8'), ctx, {{ filename: 'static/request_guard.js' }});
        vm.runInContext(fs.readFileSync({chrome_path}, 'utf8'), ctx, {{ filename: 'static/chrome.js' }});
        __out[name] = [];
        __sequences[name].forEach(function (payload) {{
            var data = JSON.parse(JSON.stringify(payload));
            if (data.__ageMs !== undefined) {{
                data.last_successful_cycle_at = new Date(serverNow - data.__ageMs).toISOString();
                delete data.__ageMs;
            }}
            var timing = {{ sentAtMono: perf, receivedAtMono: perf, serverNowMs: serverNow }};
            sandbox.window.updateChromeTicker(data, timing);
            __out[name].push({{
                text: label.textContent, title: wrap.title,
                dot: dot.style.background, color: label.style.color,
                longLabel: rootClasses['engine-label-long'] === true
            }});
        }});
    }});
    console.log(JSON.stringify(__out));
    """
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "engine_status_harness.js"
        script.write_text(harness, encoding="utf-8")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, f"static/chrome.js harness crashed:\n{result.stderr}"
    return json.loads(result.stdout.strip())


_FOUR_MIN = 4 * 60 * 1000
_TWO_HOURS = 2 * 60 * 60 * 1000
_TEN_S = 10 * 1000

_OPEN = {"market_state": "open"}
_STATIC_HEADER_CASES = {
    "blackout, last cycle four minutes old": (
        {**_OPEN, "engine_phase": "blackout", "__ageMs": _FOUR_MIN},
        ("Rebalance pause", _BLACKOUT_TITLE, _DIM, _DIM),
    ),
    "blackout, judged open by meta alone": (
        {"meta": {"system_online": True}, "engine_phase": "blackout", "__ageMs": _FOUR_MIN},
        ("Rebalance pause", _BLACKOUT_TITLE, _DIM, _DIM),
    ),
    "blackout, no cycle stamp at all": (
        {**_OPEN, "engine_phase": "blackout"},
        ("Rebalance pause", _BLACKOUT_TITLE, _DIM, _DIM),
    ),
    "opening, last cycle from the prior session": (
        {**_OPEN, "engine_phase": "opening", "__ageMs": _TWO_HOURS},
        ("Opening", _OPENING_TITLE, _DIM, _DIM),
    ),
    "opening, no cycle stamp at all": (
        {**_OPEN, "engine_phase": "opening"},
        ("Opening", _OPENING_TITLE, _DIM, _DIM),
    ),
    "running, last cycle four minutes old": (
        {**_OPEN, "engine_phase": "running", "__ageMs": _FOUR_MIN},
        ("Stale", _STALE_TITLE, "var(--studio-warn)", "var(--studio-warn)"),
    ),
    "running, last cycle ten seconds old": (
        {**_OPEN, "engine_phase": "running", "__ageMs": _TEN_S},
        ("Live", "", "var(--studio-pos)", "var(--studio-pos)"),
    ),
    "closed market beats a blackout phase": (
        {"market_state": "closed_frozen", "engine_phase": "blackout", "__ageMs": _FOUR_MIN},
        ("Closed", "", _DIM, _DIM),
    ),
    "closed market with the closed phase": (
        {"market_state": "closed_frozen", "engine_phase": "closed"},
        ("Closed", "", _DIM, _DIM),
    ),
    "no phase and a stale cycle reads as before": (
        {**_OPEN, "__ageMs": _FOUR_MIN},
        ("Stale", _STALE_TITLE, "var(--studio-warn)", "var(--studio-warn)"),
    ),
    "no phase and a fresh cycle reads as before": (
        {**_OPEN, "__ageMs": _TEN_S},
        ("Live", "", "var(--studio-pos)", "var(--studio-pos)"),
    ),
    "an unknown phase falls back to the cycle age, stale": (
        {**_OPEN, "engine_phase": "unheard-of", "__ageMs": _FOUR_MIN},
        ("Stale", _STALE_TITLE, "var(--studio-warn)", "var(--studio-warn)"),
    ),
    "an unknown phase falls back to the cycle age, fresh": (
        {**_OPEN, "engine_phase": "unheard-of", "__ageMs": _TEN_S},
        ("Live", "", "var(--studio-pos)", "var(--studio-pos)"),
    ),
}


class TestUpdateEngineStatusScheduledPauses:
    def test_each_payload_reads_its_label_tooltip_and_colours(self):
        out = _run_header(
            {name: [payload] for name, (payload, _want) in _STATIC_HEADER_CASES.items()}
        )
        wrong = []
        for name, (_payload, (text, title, dot, color)) in _STATIC_HEADER_CASES.items():
            got = out[name][-1]
            if (got["text"], got["title"], got["dot"], got["color"]) != (text, title, dot, color):
                wrong.append(f"{name}: expected {(text, title, dot, color)}, got {got}")
        assert not wrong, "\n".join(wrong)

    def test_a_poll_sequence_replaces_the_previous_label_tooltip_and_colours(self):
        blackout = {**_OPEN, "engine_phase": "blackout", "__ageMs": _FOUR_MIN}
        opening = {**_OPEN, "engine_phase": "opening", "__ageMs": _TWO_HOURS}
        fresh = {**_OPEN, "engine_phase": "running", "__ageMs": _TEN_S}
        stale = {**_OPEN, "engine_phase": "running", "__ageMs": _FOUR_MIN}
        closed = {"market_state": "closed_frozen", "engine_phase": "closed"}
        sequences = {
            "blackout then the close resumes the stops": [blackout, fresh],
            "stale then the blackout starts": [stale, blackout],
            "blackout then the market closes": [blackout, closed],
            "opening then a stale first cycle": [opening, stale],
            "opening then the first cycle completes": [opening, fresh],
        }
        want = {
            "blackout then the close resumes the stops": ("Live", "", "var(--studio-pos)"),
            "stale then the blackout starts": ("Rebalance pause", _BLACKOUT_TITLE, _DIM),
            "blackout then the market closes": ("Closed", "", _DIM),
            "opening then a stale first cycle": ("Stale", _STALE_TITLE, "var(--studio-warn)"),
            "opening then the first cycle completes": ("Live", "", "var(--studio-pos)"),
        }
        out = _run_header(sequences)
        wrong = []
        for name, (text, title, color) in want.items():
            got = out[name][-1]
            if (got["text"], got["title"], got["color"], got["dot"]) != (text, title, color, color):
                wrong.append(f"{name}: expected {(text, title, color)}, got {got}")
        assert not wrong, "\n".join(wrong)

    def test_the_long_label_class_is_set_exactly_while_the_label_reads_rebalance_pause(self):
        blackout = {**_OPEN, "engine_phase": "blackout", "__ageMs": _FOUR_MIN}
        opening = {**_OPEN, "engine_phase": "opening", "__ageMs": _TWO_HOURS}
        fresh = {**_OPEN, "engine_phase": "running", "__ageMs": _TEN_S}
        out = _run_header({"poll sequence": [fresh, blackout, blackout, opening, blackout, fresh]})
        steps = out["poll sequence"]
        assert [s["text"] for s in steps] == [
            "Live",
            "Rebalance pause",
            "Rebalance pause",
            "Opening",
            "Rebalance pause",
            "Live",
        ]
        assert [s["longLabel"] for s in steps] == [False, True, True, False, True, False], (
            "the long-label class must be set exactly while the label reads 'Rebalance pause' "
            "and cleared on the next other label"
        )
