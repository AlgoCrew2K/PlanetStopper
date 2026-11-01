"""The dashboard script never reads the browser's clock to cut a window.

When `/api/hero-chart/<window>` fails, `static/index.js` draws the picked window from the poll's
own dated series, cut at a calendar cutoff. The cutoff counts back from the session the figures
belong to, which the server states in the poll body; a browser clock that is a day off, a laptop
that woke days later, or a page left open into the next year must leave the drawn series alone.

Real bodies: `/api/state` of a closed session (and of an open one) comes from the real route over
a temp database holding a year of rows (`tests/app/_closed_session_world`), and runs in the node
harness against the real page. Only the browser clock (`now_iso`) differs between the runs.
Expected dates come from the session day and the window's day count, never from a response.
"""

from __future__ import annotations

from datetime import date, timedelta

from tests.app._closed_session_world import FRIDAY_SESSION, build_history_world
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

SCRIPTS = sup.CHROME_AND_INDEX
DEFAULT_WORLD = "default"
TRAILING_DAYS = {"90d": 90, "125d": 125, "1y": 365}

# Three browser clocks: the evening of the session, the same page days later, and the next year.
BROWSER_CLOCKS = {
    "the evening of the session": "2026-10-30T20:30:00.000Z",
    "days later": "2026-11-03T12:00:00.000Z",
    "next year": "2027-01-02T12:00:00.000Z",
}

SCENARIO = r"""
await H.boot({ extras: true, state: H.bodies.state });
H.route(/^\/api\/hero-chart\//, { reject: true });
const drawn = {};
for (const token of __TOKENS__) {
  await H.click('[data-testid="window-' + token + '"]');
  await H.completeAnimations();
  drawn[token] = H.summary()['cum-chart'].labels;
}
return { drawn: drawn };
"""


def run_page(state: dict, browser_clock: str, tokens: list[str]) -> dict:
    scenario = SCENARIO.replace("__TOKENS__", str(tokens))
    return sup.run(
        scenario,
        world=DEFAULT_WORLD,
        scripts=SCRIPTS,
        extra_bodies={"state": state},
        now_iso=browser_clock,
        timeout=420,
    )["drawn"]


def expected_labels(state: dict, token: str, anchor: date) -> list[str]:
    """The poll's own dates inside the window that counts back from `anchor`."""
    dates, bot, _held = hx.poll_hist(state)
    if token == "ytd":
        cutoff = date(anchor.year, 1, 1)
    else:
        cutoff = anchor - timedelta(days=TRAILING_DAYS[token])
    labels, _ = hx.rebase_to_window(dates, bot, cutoff.isoformat())
    return labels


def check_page(state: dict, anchor: date, tokens: list[str]) -> list[str]:
    wrong = []
    for clock_name, browser_clock in BROWSER_CLOCKS.items():
        drawn = run_page(state, browser_clock, tokens)
        for token in tokens:
            want = expected_labels(state, token, anchor)
            if drawn[token] != want:
                wrong.append(
                    f"browser clock {clock_name} ({browser_clock}), window {token}: drew "
                    f"{drawn[token][:1]}..{drawn[token][-1:]} ({len(drawn[token])} days), expected "
                    f"{want[:1]}..{want[-1:]} ({len(want)} days) counting back from {anchor}"
                )
    return wrong


def test_a_closed_session_draws_the_same_window_whatever_the_browser_clock_says(
    monkeypatch, tmp_path
):
    hx.get_world(DEFAULT_WORLD)  # built before this test's rows exist
    cw = build_history_world(monkeypatch, tmp_path)
    cw.at("2026-10-30", "16:30:00")
    state = cw.poll("/api/state")
    assert state["market_state"] == "closed_frozen"
    anchor = date.fromisoformat(FRIDAY_SESSION.session_day)
    dates, _bot, _held = hx.poll_hist(state)
    assert date.fromisoformat(dates[0]) < anchor - timedelta(days=125), (
        "fixture sanity: the poll series reaches back past the longest window it can draw whole"
    )
    wrong = check_page(state, anchor, ["ytd", "90d", "125d", "1y"])
    assert not wrong, "\n".join(wrong)


def test_an_open_session_draws_the_same_window_whatever_the_browser_clock_says(
    monkeypatch, tmp_path
):
    hx.get_world(DEFAULT_WORLD)
    cw = build_history_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local("2026-11-02", "10:00:20"), 1)
    cw.at("2026-11-02", "10:01:00")
    state = cw.poll("/api/state")
    assert state["market_state"] == "open"
    anchor = date.fromisoformat("2026-11-02")
    wrong = check_page(state, anchor, ["ytd", "90d", "125d", "1y"])
    assert not wrong, "\n".join(wrong)
