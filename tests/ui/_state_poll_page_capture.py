"""What the dashboard shows and requests when it applies a `GET /api/state` body.

TEST-ONLY. One implementation, called by the checking test and by the baseline producer, so the
capture a head is compared with is read the same way it was written.

Each scenario builds one named world of `tests/app/_state_poll_worlds.py` (the real writers over
a per-test database, the route clock fixed), serves the app in this process
(`tests/ui/_browser_server.py`) and opens `/` in Firefox headless at a fixed viewport and theme
with the browser clock installed at the world's instant. Every request to anything but that
server is refused (`tests/ui/_shell_support.open_probe`) and the developer `.env` is never read
(the world points `app.ENV_FILE_PATH` at an absent file and removes every credential variable).

A scenario record holds what an operator can see and what the page asks the server:

* `page_text`: the normalized visible text of the page wrap (every figure, label, card, panel);
* `detail_text`, `footer`, `risk_math_velocity`: the detail panel's text, its identity footer and
  the Risk Math velocity rows, when the scenario opens a card;
* `state_requests`: the `/api/state` requests (path and query) the page made from the start of
  one poll to the next increment of `body[data-refresh-count]`, in order;
* `other_requests`: every other request of that window, sorted (concurrent requests have no
  order);
* `engine_label`, `as_of`, `final_path`: the header status label, the freshness text and the path
  the page ends on (a 401 sends it to the login page).

No literal figure is typed anywhere: a check compares a head's record with the record the
reference commit's page produced for the same world.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from tests.app._state_poll_worlds import WORLDS
from tests.ui._browser_server import running_server
from tests.ui._shell_support import NARROW, READY_SELECTOR, READY_TIMEOUT_MS, WIDE, open_probe

_CREDENTIAL_VARIABLES = (
    "LIVE_EXECUTION",
    "EXIT_AUTHORITY",
    "EXECUTION_START_TIME",
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
)

FALLBACK_POLL_MS = 30_000
WAIT_MS = 20_000
SSE_CONNECT_TIMEOUT_S = 15.0
QUIET_MS = 600
POLL_STEP_MS = 100

_READ_PAGE = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const text = (el) => (el ? norm(el.innerText) : null);
  const wrap = document.querySelector('.page-wrap') || document.body;
  const rows = (id) => Array.from(document.querySelectorAll(`#${id}`)).map(text);
  return {
    page_text: text(wrap),
    detail_text: text(document.getElementById('detail-panel')),
    footer: text(document.getElementById('dp-footer-id')),
    risk_math_velocity: {
      value: text(document.getElementById('dp-rm-para')),
      hint: text(document.getElementById('dp-rm-para-hint')),
    },
    engine_label: text(document.getElementById('engine-status-label')),
    as_of: text(document.getElementById('hero-data-as-of')),
    cards: Array.from(document.querySelectorAll('[data-testid="sym-card"][data-sym-id]'))
      .map((card) => ({ id: card.getAttribute('data-sym-id'), text: text(card) }))
      .sort((a, b) => (a.id < b.id ? -1 : 1)),
    refresh_count: document.body.dataset.refreshCount || null,
    ready_rows: rows('hero-tracked'),
  };
}
"""


@dataclass(frozen=True)
class Scenario:
    world: str
    width: int
    height: int
    theme: str | None
    mode: str  # load, fallback, sse, detail, poll_500, poll_401


SCENARIOS: dict[str, Scenario] = {
    "open_first_poll_wide_light": Scenario("open_two_accounts", *WIDE, None, "load"),
    "closed_first_poll_wide_light": Scenario("closed_frozen_two_accounts", *WIDE, None, "load"),
    "open_sse_refresh": Scenario("open_two_accounts", *WIDE, None, "sse"),
    "open_detail_panel_poll": Scenario("open_two_accounts", *WIDE, None, "detail"),
    "open_poll_answered_500": Scenario("open_two_accounts", *WIDE, None, "poll_500"),
    "open_poll_answered_401": Scenario("open_two_accounts", *WIDE, None, "poll_401"),
    "closed_first_poll_narrow_dark": Scenario(
        "closed_frozen_two_accounts", *NARROW, "dark", "load"
    ),
    "no_state_first_load": Scenario("no_state", *WIDE, None, "load"),
    "open_fallback_poll": Scenario("open_two_accounts", *WIDE, None, "fallback"),
}


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class _InFlight:
    """The requests the page has sent and not yet seen end, leaving out the event stream (it ends
    only when the page does)."""

    def __init__(self, page) -> None:
        self.pending: set = set()
        page.on("request", self._start)
        page.on("requestfinished", self._end)
        page.on("requestfailed", self._end)

    def _start(self, request) -> None:
        if "/api/events" not in request.url:
            self.pending.add(request)

    def _end(self, request) -> None:
        self.pending.discard(request)

    def settle(self, page) -> None:
        """Wait until nothing has been in flight for a whole quiet period, so every figure that a
        late answer fills in (the guard effect, the risk profile) has been written."""
        quiet_ms = 0
        waited_ms = 0
        while quiet_ms < QUIET_MS:
            page.wait_for_timeout(POLL_STEP_MS)
            waited_ms += POLL_STEP_MS
            quiet_ms = 0 if self.pending else quiet_ms + POLL_STEP_MS
            if waited_ms > WAIT_MS:
                raise AssertionError(
                    f"requests still in flight: {sorted(r.url for r in self.pending)}"
                )


def _wait_refresh(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.body.dataset.refreshCount === String(n)", arg=count, timeout=WAIT_MS
    )


def _settled_read(page, in_flight: _InFlight) -> dict:
    in_flight.settle(page)
    return page.evaluate(_READ_PAGE)


def _since(requests: list[str], mark: int) -> dict:
    window = requests[mark:]
    return {
        "state_requests": [r for r in window if r.startswith("/api/state")],
        "other_requests": sorted(r for r in window if not r.startswith("/api/state")),
    }


def _wait_for_event_stream(app_module) -> None:
    deadline = time.monotonic() + SSE_CONNECT_TIMEOUT_S
    while time.monotonic() < deadline:
        with app_module._sse_clients_lock:
            if app_module._sse_clients:
                return
        time.sleep(0.05)
    raise AssertionError("the page never opened its event stream")


def _put_cycle_complete(app_module) -> None:
    """The fan-out step of `_notify_cycle_complete` and nothing before it: the cache masking and
    the Composer refresh that precede it would call out of this process."""
    payload = json.dumps({"changed": True})
    with app_module._sse_clients_lock:
        clients = list(app_module._sse_clients)
    assert clients, "no stream is connected"
    for queue in clients:
        queue.put_nowait(("cycle-complete", payload))


def capture_scenario(firefox, name: str, monkeypatch, tmp_path) -> dict:
    """The record of one scenario. `monkeypatch` and `tmp_path` are the test's own."""
    import app as app_module

    spec = SCENARIOS[name]
    for variable in _CREDENTIAL_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    built = WORLDS[spec.world](monkeypatch, tmp_path)
    instant = built.inner.moment.instant

    with (
        running_server() as server,
        open_probe(
            firefox, server, width=spec.width, height=spec.height, theme=spec.theme
        ) as probe,
    ):
        page = probe.page
        requests: list[str] = []
        page.on("request", lambda request: requests.append(_path_and_query(request.url)))
        in_flight = _InFlight(page)
        probe.context.clock.install(time=instant)
        page.goto(server.base_url + "/")
        page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
        _wait_refresh(page, 1)
        record: dict = {"world": spec.world, "mode": spec.mode}

        if spec.mode == "load":
            record.update(_settled_read(page, in_flight))
        elif spec.mode == "fallback":
            mark = len(requests)
            probe.context.clock.run_for(FALLBACK_POLL_MS)
            _wait_refresh(page, 2)
            record.update(_settled_read(page, in_flight))
            record.update(_since(requests, mark))
        elif spec.mode == "sse":
            _wait_for_event_stream(app_module)
            mark = len(requests)
            _put_cycle_complete(app_module)
            _wait_refresh(page, 2)
            record.update(_settled_read(page, in_flight))
            record.update(_since(requests, mark))
        elif spec.mode == "detail":
            page.click('[data-testid="sym-card"][data-sym-id]')
            page.wait_for_selector("#detail-panel.open", timeout=WAIT_MS)
            record["before_poll"] = _settled_read(page, in_flight)
            mark = len(requests)
            page.evaluate("window.loadState()")
            _wait_refresh(page, 2)
            record.update(_settled_read(page, in_flight))
            record.update(_since(requests, mark))
        elif spec.mode == "poll_500":
            page.route(
                "**/api/state*",
                lambda route: route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"status": "error", "message": "An internal error occurred"}',
                ),
            )
            page.evaluate("window.loadState()")
            _wait_refresh(page, 2)
            record.update(_settled_read(page, in_flight))
        elif spec.mode == "poll_401":
            page.route(
                "**/api/state*",
                lambda route: route.fulfill(
                    status=401,
                    content_type="application/json",
                    body='{"error": "unauthenticated"}',
                ),
            )
            page.evaluate("window.loadState()")
            page.wait_for_function("() => location.pathname !== '/'", timeout=WAIT_MS)
        else:
            raise AssertionError(f"no mode {spec.mode}")

        record["final_path"] = urlsplit(page.url).path
        record["page_errors"] = list(probe.page_errors)
        return record


def firefox_version(firefox) -> str:
    return firefox.version
