"""RED: an open market with no cycle committed since the open shows the last session's figures.

While the market is open and the last cycle the engine committed is older than today's open (every
morning from the open until the first cycle lands), the words of every page name the last session
(`test_open_market_stalled_session_wording`). These tests pin the numbers under those words: for the
same stored state, every figure on every page and API at a stalled-open instant equals the figure the
pre-market view serves at 09:29 ET, the same stored state before the open, and, where the figures
are independent of the clock, the figure the close served at 16:30 the evening before.

Method: the virtual-clock world of `tests/app/_closed_session_world` (captured shadow rows through the
real writers, the close snapshot and the close cycle in `bot_state`, the real XNYS calendar, the real
routes through the Flask test client), plus one Managed Sleeves rule that fired on the closed
session. Nothing is typed in: each baseline is the response itself at another instant of the same
world. Mon 09:30:30 is the Opening phase, 09:40 the Degraded phase, 13:00 mid-session.

What is compared, per route (figures, never whole bodies):

* `GET /`: the hero Today, Cumulative and Max DD rows, the headline and the counts (`rendered_page_figures`),
  every card figure and every Sleeves count (`FIELD_FIGURES`, `TESTID_FIGURES`, as sorted lists so card
  order is not a figure), and the chart data the page embeds for its first paint.
* `/api/state`: the portfolio strip, each card by id, each state entry's figure keys, `meta.portfolio`,
  the three counts, the divergence, the dashboard panels, the as-of stamp and the trading day.
* `/api/strip/<w>`, `/api/hero-chart/<w>`, `/api/guard-alpha-summary[?window=<w>]`,
  `/api/history/<n>`, `/api/exit-turnover`, `/api/performance`, `/api/performance/symphonies`,
  `/api/sleeves`: the whole body (these carry no clock field).

Excluded, each because it describes the clock or the view rather than a figure: `market_state` and
`meta.market_state*` (the state the clock is in), `meta.clock_et` and `next_run*` (the clock and the
countdown), `meta.system_online` and `engine_phase` (the header), `degraded_reasons` (the Degraded
header's reason), `frozen_at` and `snapshot_is_previous_session` (labels of a frozen view),
`last_successful_cycle_at` and `execution_start_time` (the stamp and the config, not figures), the raw
`state` and `bot_state` dumps beyond the figure keys above (engine telemetry such as `para_velocity`),
`accounts_map` and `html` (the legacy table partial, which the dashboard script does not read), and
the labels `rule-today-label` and `dv-label-bot` (words, pinned by the wording tests).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest

import database as database_module
from tests.app._closed_session_world import WINDOW_TOKENS, ClosedWorld
from tests.app._open_session_wording import FRIDAY, MONDAY, TUESDAY, stalled_world
from tests.app._refresh_timing_frozen_world import describe, differences
from tests.app._refresh_timing_state_world import (
    SYMPHONY_IDS,
    TRIGGERED_ID,
    rendered_page_figures,
)
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

PRE_MARKET_CLOCK = "09:29:00"
CLOSE_EVENING_CLOCK = "16:30:00"
# The Opening phase, the Degraded phase and mid-session.
STALLED_CLOCKS = ("09:30:30", "09:40:00", "13:00:00")

# A fire on the closed session, in the stored UTC form (14:00 ET), so the Sleeves panel has a
# per-day count that belongs to that session and not to the new one.
SLEEVE_FIRE_UTC = "2026-10-30 18:00:00"

# Card and Sleeves elements whose text is a figure. Absent elements read as an empty list on both
# sides of a comparison; the precondition tests prove the ones that matter are present.
FIELD_FIGURES = (
    "tc-bot",
    "tc-held",
    "tc-alpha-badge",
    "cr-bot",
    "cr-held",
    "cr-alpha-badge",
    "mdd-bot",
    "mdd-held",
    "mdd-alpha-badge",
    "mdd-lifetime",
    "verdict-text",
    "rule-today",
    "rule-lifetime",
    "rule-realized",
    "sleeve-realized",
    "sleeve-capital",
    "sleeve-ledger-cash",
)
TESTID_FIGURES = (
    "comp-today-bot-text",
    "comp-today-held-text",
    "comp-today-delta",
    "comp-cumulative-bot-text",
    "comp-cumulative-held-text",
    "comp-cumulative-delta",
    "comp-mdd-bot-text",
    "comp-mdd-held-text",
    "guard-alpha-headline",
    "account-all-time-cr",
    "status-pill",
)
# The keys of a `state` entry that are figures of the session (not engine telemetry).
STATE_ENTRY_FIGURES = (
    "current_return",
    "current_value",
    "guard_alpha",
    "simple_return",
    "time_weighted_return",
    "max_drawdown",
    "armed",
    "triggered",
    "triggered_reason",
    "_tc",
    "_cr",
    "_mdd",
)
HISTORY_DAYS = ("30", "90", "365", "ytd")
PERFORMANCE_WINDOWS = ("60d", "ytd")


# ---------------------------------------------------------------------------
# Reading what an operator can see, by route
# ---------------------------------------------------------------------------


def _marks(page: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for attribute, names in (("data-field", FIELD_FIGURES), ("data-testid", TESTID_FIGURES)):
        for name in names:
            found = re.findall(rf'{attribute}="{re.escape(name)}"[^>]*>([^<]*)<', page)
            out[f"{attribute}:{name}"] = sorted(
                " ".join(html_lib.unescape(text).split()) for text in found
            )
    return out


def _initial_data(page: str) -> dict:
    found = re.search(r'<script[^>]*id="dashboard-initial-data"[^>]*>(.*?)</script>', page, re.S)
    assert found, "the page embeds no first paint data"
    return json.loads(found.group(1))


def read_page(cw: ClosedWorld) -> dict:
    page = cw.raw("/").decode("utf-8")
    return {
        "rows_and_headline": rendered_page_figures(page),
        "marks": _marks(page),
        "first_paint_data": _initial_data(page),
    }


def _is_symphony_entry(value: object) -> bool:
    return isinstance(value, dict) and "name" in value


def read_state(cw: ClosedWorld) -> dict:
    body = cw.poll("/api/state")
    meta = body["meta"]
    return {
        "portfolio_strip": body["portfolio_strip"],
        "cards": {card["id"]: card for card in body["symphonies"]},
        "state_entries": {
            sym_id: {key: entry.get(key) for key in STATE_ENTRY_FIGURES}
            for sym_id, entry in body["state"].items()
            if _is_symphony_entry(entry)
        },
        "meta_portfolio": meta["portfolio"],
        "counts": {key: meta[key] for key in ("tracked", "armed", "triggered")},
        "shadow_divergence": body.get("shadow_divergence"),
        "dashboard_panels": body.get("dashboard_panels"),
        "data_as_of": body.get("data_as_of"),
        "trading_day": body.get("trading_day"),
    }


def read_windows(cw: ClosedWorld) -> dict:
    return {
        kind: {token: cw.poll(f"/api/{kind}/{token}") for token in WINDOW_TOKENS}
        for kind in ("strip", "hero-chart")
    }


def read_saved(cw: ClosedWorld) -> dict:
    out = {"all time": cw.poll("/api/guard-alpha-summary")}
    for token in WINDOW_TOKENS:
        out[token] = cw.poll(f"/api/guard-alpha-summary?window={token}")
    return out


def read_history(cw: ClosedWorld) -> dict:
    return {days: cw.poll(f"/api/history/{days}") for days in HISTORY_DAYS}


def read_exit_turnover(cw: ClosedWorld) -> dict:
    return {sym_id: cw.poll(f"/api/exit-turnover?symphony_id={sym_id}") for sym_id in SYMPHONY_IDS}


def read_performance(cw: ClosedWorld) -> dict:
    out: dict = {"symphonies": cw.poll("/api/performance/symphonies")}
    for days in PERFORMANCE_WINDOWS:
        out[f"aggregate {days}"] = cw.poll(f"/api/performance?days={days}")
        out[f"symphony {days}"] = cw.poll(
            f"/api/performance?scope=symphony&symphony_id={TRIGGERED_ID}&days={days}"
        )
    return out


def read_sleeves(cw: ClosedWorld) -> dict:
    return {"rows": cw.poll("/api/sleeves")}


READERS: dict[str, Callable[[ClosedWorld], dict]] = {
    "page": read_page,
    "state poll": read_state,
    "strip and hero windows": read_windows,
    "dollar saved panel": read_saved,
    "history": read_history,
    "exit turnover": read_exit_turnover,
    "performance": read_performance,
    "sleeves": read_sleeves,
}
GROUPS = list(READERS)


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


def seed_sleeve(label: str = "Probe") -> None:
    """One sleeve with one rule that fired on the closed session (and on no other day)."""
    sleeve_id = database_module.create_sleeve(f"{label} sleeve", 1000.0)
    rule_id = database_module.create_sleeve_rule(sleeve_id, f"{label} rule")
    database_module.insert_sleeve_rule_fire(
        rule_id, sleeve_id, "notify", "DEFENSIVE", "SHADOW", fired_at=SLEEVE_FIRE_UTC
    )


def fresh_world(monkeypatch, directory, *, sleeve: bool = True) -> ClosedWorld:
    """A stalled-morning world in its own database under `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DB_PATH", str(directory / "state.db"))
    database_module.init_db()
    cw = stalled_world(monkeypatch, directory)
    if sleeve:
        seed_sleeve()
    return cw


def read_at(cw: ClosedWorld, group: str, day: str, clock: str) -> dict:
    cw.at(day, clock)
    return READERS[group](cw)


def assert_same_figures(where: str, expected: dict, actual: dict, against: str) -> None:
    changed = differences(expected, actual)
    assert not changed, (
        f"{where}: {len(changed)} figures differ from {against}: {describe(changed, 8)}"
    )


@dataclass(frozen=True)
class Scenario:
    name: str
    stalled_day: str
    # Extra baselines the figures must also equal, as (label, day, clock): the close's own evening.
    also_equal: tuple[tuple[str, str, str], ...]
    prepare: Callable[[ClosedWorld], None]


def _no_prep(_cw: ClosedWorld) -> None:
    return None


def _monday_evening_cycle(cw: ClosedWorld) -> None:
    write_engine_cycle(cw.world, local(MONDAY, "20:00:05"), 1)


FRIDAY_CLOSE = Scenario(
    "friday close, stalled monday",
    MONDAY,
    (("the Friday 16:30 close", FRIDAY, CLOSE_EVENING_CLOCK),),
    _no_prep,
)
MONDAY_EVENING = Scenario(
    "monday evening cycle, stalled tuesday", TUESDAY, (), _monday_evening_cycle
)
SCENARIOS = (FRIDAY_CLOSE, MONDAY_EVENING)


# ---------------------------------------------------------------------------
# The rule: a stalled open serves the last session's figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", GROUPS)
def test_precondition_the_pre_market_view_and_the_close_serve_the_same_figures(
    monkeypatch, tmp_path, group
):
    """The baselines agree with each other, so the comparisons below hold the stalled open to one
    set of figures and the exclusions in the module docstring are the only clock fields."""
    cw = fresh_world(monkeypatch, tmp_path)
    close = read_at(cw, group, FRIDAY, CLOSE_EVENING_CLOCK)
    pre = read_at(cw, group, MONDAY, PRE_MARKET_CLOCK)

    assert_same_figures(f"{group}, Monday {PRE_MARKET_CLOCK} ET", close, pre, "the Friday close")


def test_precondition_the_figures_read_are_present_and_the_session_has_something_to_show(
    monkeypatch, tmp_path
):
    cw = fresh_world(monkeypatch, tmp_path)
    cw.at(FRIDAY, CLOSE_EVENING_CLOCK)
    page = read_page(cw)
    marks = page["marks"]
    for name in ("data-field:tc-bot", "data-field:tc-held", "data-field:rule-today"):
        assert any(text and text != "\u2014" for text in marks[name]), (
            f"fixture: the close shows no {name} figure, so no comparison on it means anything"
        )
    assert page["rows_and_headline"]["today_bot"] is not None
    assert page["rows_and_headline"]["guard_alpha"] is not None
    assert "1" in marks["data-field:rule-today"], "fixture: the rule fired on the closed session"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.parametrize("clock", STALLED_CLOCKS)
@pytest.mark.parametrize("group", GROUPS)
def test_a_stalled_open_shows_the_figures_of_the_pre_market_view_on_every_route(
    monkeypatch, tmp_path, group, clock, scenario
):
    cw = fresh_world(monkeypatch, tmp_path)
    scenario.prepare(cw)
    baselines = [
        ("the pre-market view", read_at(cw, group, scenario.stalled_day, PRE_MARKET_CLOCK))
    ] + [(label, read_at(cw, group, day, at)) for label, day, at in scenario.also_equal]

    seen = read_at(cw, group, scenario.stalled_day, clock)

    assert cw.poll("/api/state")["market_state"] == "open", "fixture: the market is open"
    for label, expected in baselines:
        assert_same_figures(
            f"{group}, {scenario.stalled_day} {clock} ET, stalled", expected, seen, label
        )


def test_the_today_row_and_the_cards_read_the_close_sessions_numbers_not_a_dash(
    monkeypatch, tmp_path
):
    """Each of the hero Today row, the cards' Bot and Held and the Today guard alpha, on the first
    paint and on the poll, is the number the close showed: Bot is not a dash and Held is not the
    new morning's reading."""
    cw = fresh_world(monkeypatch, tmp_path)
    close_page = read_at(cw, "page", FRIDAY, CLOSE_EVENING_CLOCK)
    close_state = read_state(cw)
    assert close_page["rows_and_headline"]["today_bot"] is not None, "fixture: the close has a Bot"

    cw.at(MONDAY, "09:40:00")
    page = read_page(cw)
    state = read_state(cw)

    for name in ("today_bot", "today_held", "guard_alpha"):
        assert page["rows_and_headline"][name] == close_page["rows_and_headline"][name], (
            f"the page's {name} reads {page['rows_and_headline'][name]!r}, the close's was "
            f"{close_page['rows_and_headline'][name]!r}"
        )
    for name in (
        "data-field:tc-bot",
        "data-field:tc-held",
        "data-testid:comp-today-bot-text",
        "data-testid:comp-today-held-text",
        "data-testid:comp-today-delta",
    ):
        assert page["marks"][name] == close_page["marks"][name], (
            f"{name}: {page['marks'][name]} at Monday 09:40, {close_page['marks'][name]} at the close"
        )
    for sym_id, card in close_state["cards"].items():
        for key in ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held"):
            assert state["cards"][sym_id][key] == card[key], (
                f"card {sym_id} {key}: {state['cards'][sym_id][key]!r} on the poll, "
                f"{card[key]!r} at the close"
            )
    assert (
        state["portfolio_strip"]["today_change"] == close_state["portfolio_strip"]["today_change"]
    )
    assert state["portfolio_strip"]["guard_alpha"] == close_state["portfolio_strip"]["guard_alpha"]


# ---------------------------------------------------------------------------
# The live open view comes back with the first cycle, unchanged
# ---------------------------------------------------------------------------


def read_everything(cw: ClosedWorld) -> dict:
    return {group: reader(cw) for group, reader in READERS.items()}


def test_the_response_after_the_first_cycle_is_the_live_open_view_whatever_was_read_before(
    monkeypatch, tmp_path
):
    """One world is read through the stalled morning, then a cycle commits; an identical world
    never read before the cycle is the reference. The next response of the first equals the
    reference's, every figure of every route: no stalled view lingers, nothing is cached across
    the commit, and the figures are not the pre-market view's."""
    primed = fresh_world(monkeypatch, tmp_path / "primed")
    pre_market = read_at(primed, "state poll", MONDAY, PRE_MARKET_CLOCK)
    for clock in ("09:30:30", "09:40:00"):
        primed.at(MONDAY, clock)
        read_everything(primed)
    write_engine_cycle(primed.world, local(MONDAY, "09:40:20"), 1)
    primed.at(MONDAY, "09:41:10")
    after_commit = read_everything(primed)

    reference = fresh_world(monkeypatch, tmp_path / "reference")
    write_engine_cycle(reference.world, local(MONDAY, "09:40:20"), 1)
    reference.at(MONDAY, "09:41:10")
    live_open_view = read_everything(reference)

    assert_same_figures(
        "Monday 09:41:10 ET, a cycle committed at 09:40:20",
        live_open_view,
        after_commit,
        "an identical world that was never read before the cycle",
    )
    assert after_commit["state poll"]["portfolio_strip"] != pre_market["portfolio_strip"], (
        "fixture: once a cycle has committed the strip is the live one, not the pre-market view's"
    )


@pytest.mark.parametrize("group", ["page", "state poll", "strip and hero windows"])
def test_an_open_market_with_no_cycle_stamp_at_all_serves_the_live_view_not_the_last_sessions(
    monkeypatch, tmp_path, group
):
    """No stamp means no session to name, so the view is today's, as the wording is: an implementation
    that serves the frozen view whenever the market is open reads the pre-market figures here."""
    cw = fresh_world(monkeypatch, tmp_path)
    state = database_module.load_state()
    state.pop("last_successful_cycle_at")
    database_module.save_state(state)
    assert "last_successful_cycle_at" not in database_module.load_state(), "fixture: stamp removed"
    pre = read_at(cw, group, MONDAY, PRE_MARKET_CLOCK)

    seen = read_at(cw, group, MONDAY, "09:40:00")

    assert differences(pre, seen), (
        f"{group}: with no cycle stamp the open market shows the pre-market view's figures"
    )


@pytest.mark.parametrize("clock", STALLED_CLOCKS)
@pytest.mark.parametrize("group", ["page", "state poll", "strip and hero windows"])
def test_a_stalled_open_with_no_stored_snapshot_behaves_as_the_pre_market_view_with_none(
    monkeypatch, tmp_path, group, clock
):
    cw = fresh_world(monkeypatch, tmp_path)
    state = database_module.load_state()
    state.pop("last_market_close_snapshot")
    database_module.save_state(state)
    assert "last_market_close_snapshot" not in database_module.load_state(), "fixture: none stored"
    pre = read_at(cw, group, MONDAY, PRE_MARKET_CLOCK)

    seen = read_at(cw, group, MONDAY, clock)

    assert_same_figures(
        f"{group}, Monday {clock} ET, stalled with no snapshot", pre, seen, "the pre-market view"
    )
